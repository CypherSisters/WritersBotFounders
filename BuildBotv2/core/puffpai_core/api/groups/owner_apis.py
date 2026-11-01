"""Core API group ``owner_apis`` (lane add-api): the owner adds any search API as a Toolsmith search source.

These owner routes are the only writers of ``<state>/settings/owner_apis.json``. No token Toolsmith, a package or the
brain holds reaches them. The rows, their checks and her Yes: ``zone3/toolsmith_host/owner_apis.py``.

- ``GET /v1/toolsmith/apis``: every row with its state: tested, her Yes, key linked, on, its line. Never a key.
- ``POST /v1/toolsmith/apis`` ``{name, search, n, key_in, key_name, key_prefix, fields}``: add one; a refusal is 400 with one plain
  line. A row with no key raises its Yes card here; a row with a key raises it when the key is saved.
- ``PUT /v1/toolsmith/apis/<id>``: edit. A changed address or key place takes her old Yes back and ``tested`` goes. A row
  with no live Yes asks again here when it takes no key, or its key is already saved.
- ``DELETE /v1/toolsmith/apis/<id>`` ``{remove_key}``: her Yes goes, whatever else happens. A row with a key must say
  whether its key goes too.
- ``PUT /v1/toolsmith/apis/<id>/key`` ``{value}``: kept in the vault as ``toolsmith.api.<id>``, never in chat.
- ``DELETE /v1/toolsmith/apis/<id>/key``: her Yes goes, whatever else happens, then the key.
- ``POST /v1/toolsmith/apis/<id>/test`` ``{word}``: one search under her own act of pressing Test (``test_owner_api``).

Her Yes names one host and the row's hash. It never turns Toolsmith's search on (``bridge.is_key_yes``): with the search
off, the row waits. No route returns, echoes or logs a key.
"""

from __future__ import annotations

from flask import Blueprint, current_app

from ...zone3.toolsmith_host import owner_apis as apis
from ...zone3.toolsmith_host.service import Paused, web_key_ok
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route
from .github import _archive, _vault_state
from .zones import _body, _by, _key_state, _no_store, _search_cards

bp = Blueprint("owner_apis", __name__)
LANE = "add-api"
NO_ROW_LINE = "There is no such API."
ASK_KEY_LINE = "Say whether to take its key out of your vault too."


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _zone():
    from ...zone3.service import for_app
    return for_app(current_app)


def _state_dir():
    return current_core().state


def _take_back(z, api_id: str, why: str) -> bool:
    """Every card and every Yes for this row, whatever address each was for."""
    done = False
    for rec in _search_cards(z, "pending"):
        if apis.names(rec, api_id):
            done = z.consents.expire(rec["consent_id"], why) or done
    for rec in _search_cards(z, "granted"):
        if apis.names(rec, api_id):
            done = z.consents.revoke(rec["consent_id"]) or done
    return done


def _ask(z, row: dict) -> dict:
    """This row's own Yes card, or what is already there: no second card, and never one once her Yes stands."""
    from ...consent import ConsentError, card_view
    from ...wiring import WizardBridge
    if apis.yes_of(row, z.consents.live("host")):
        return {"yes": True}
    sha = apis.row_sha(row)
    waiting = [r for r in _search_cards(z, "pending") if apis.names(r, row["id"])
               and (r.get("covers") or {}).get("owner_api", {}).get("sha") == sha]
    try:
        rec = waiting[-1] if waiting else z.consents.ask("host", WizardBridge.SEARCH, apis.covers(row))
    except ConsentError as exc:
        return {"yes": False, "error": str(getattr(exc, "message", "") or exc)}
    seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor")))
    return {"yes": False, "consent_id": rec["consent_id"], "card_sha": rec.get("card_sha"), "title": seen["title"],
            "lines": seen["lines"]}


def _state(row: dict, archive, search_on: bool, records: list[dict]) -> dict:
    name = row["name"]
    yes = apis.yes_of(row, records) is not None
    kept = _key_state(archive, row["key"]) if row["key"] else None
    moved = bool(row["key"]) and not apis.key_bound(row)       # a key saved for another address is no key here
    linked = False if moved else kept
    keyed_ok = not row["key"] or linked is True
    on = bool(row["tested"] and yes and keyed_ok and search_on)
    if on:
        line = f"On. Toolsmith searches {name} while it makes a plan. Only search words go out."
    elif moved and (row["key_host"] or kept is True):
        line = apis.MOVED_KEY_LINE
    elif row["key"] and linked is False:
        line = f"Waiting for your key, so {name} stays off."
    elif row["key"] and linked is None:
        line = f"Your vault is locked, so BuildBot can't tell whether your key is kept. {name} stays off."
    elif not yes:
        line = f"Waiting for your Yes, so {name} stays off. Save it again to see its card."
    elif not row["tested"]:
        line = f"Off until a Test reads results from {name}. Press Test."
    else:
        line = f"Waiting: Toolsmith's search is off, so {name} is off too."
    return {"id": row["id"], "name": name, "host": row["host"], "search": row["search"], "key_in": row["key_in"],
            "key_name": row["key_name"], "key_prefix": row["key_prefix"], "fields": row["fields"],
            "tested": row["tested"], "yes": yes,
            "linked": linked, "on": on, "line": line}


def listed(z) -> list[dict]:
    """Every row with its state. Also on ``GET /v1/toolsmith/hosts``. Never a key."""
    from ...wiring import WizardBridge
    a, view = _archive(), z.bridge.view({})
    search_on = view.get("setting") == "open" and bool((view.get("switches") or {}).get(WizardBridge.SEARCH))
    records = z.consents.live("host")
    return [_state(row, a, search_on, records) for row in apis.rows(_state_dir()).values()]


@built("GET", "/v1/toolsmith/apis", OWNER)
def apis_get():
    return _no_store({"apis": listed(_zone()), "vault": _vault_state(_archive()) or "not_set_up"})


@built("POST", "/v1/toolsmith/apis", OWNER_DEVICE)
def apis_post():
    try:
        row = apis.add(_state_dir(), _body())
    except apis.ApiRefused as exc:
        return _no_store({"error": str(exc), "line": str(exc)}, 400)
    out = {"id": row["id"], "line": f"Added {row['name']}. It stays off until your Yes and a Test that reads results."}
    if not row["key"]:
        out.update(_ask(_zone(), row))
    else:
        out["line"] = f"Added {row['name']}. Link its key next. It stays off until your Yes and a Test that reads results."
    return _no_store(out, 201)


@built("PUT", "/v1/toolsmith/apis/<api_id>", OWNER_DEVICE)
def api_put(api_id: str):
    state = _state_dir()
    old = apis.rows(state).get(api_id)
    if old is None:
        return _no_store({"error": NO_ROW_LINE}, 404)
    z = _zone()
    try:
        row, changed = apis.edit(state, api_id, _body())
    except apis.ApiRefused as exc:
        return _no_store({"error": str(exc), "line": str(exc)}, 400)
    if changed:
        _take_back(z, api_id, "You changed this API's address or where its key goes.")
    out = {"id": api_id, "line": (f"Saved {row['name']}. You changed where it sends, so it asks again and needs a new Test."
                                  if changed else f"Saved {row['name']}.")}
    if not row["key"] or (apis.key_bound(row) and _key_state(_archive(), row["key"]) is True):
        out.update(_ask(z, row))
    elif row["key_host"] and not apis.key_bound(row):
        out["line"] += f" {apis.MOVED_KEY_LINE}"
    if not row["key"] and old["key"]:
        out["line"] += " Its old key stays in your vault until you remove it."
    return _no_store(out)


def _remove_key(name: str, vault_name: str) -> tuple[bool | None, str]:
    """``(gone, line)``: None when the vault must open first, or could not do it."""
    from ...zone1.vault import VaultError
    a = _archive()
    if not a.is_open():
        return None, "Unlock your vault to take the key out too."
    try:
        return bool(a.delete_secret(vault_name)), ""
    except VaultError as exc:
        return None, str(exc.message)


@built("DELETE", "/v1/toolsmith/apis/<api_id>", OWNER_DEVICE)
def api_delete(api_id: str):
    state = _state_dir()
    row = apis.rows(state).get(api_id)
    if row is None:
        return _no_store({"error": NO_ROW_LINE}, 404)
    remove_key = _body().get("remove_key")
    if row["key"] and not isinstance(remove_key, bool):
        return _no_store({"error": ASK_KEY_LINE, "line": ASK_KEY_LINE, "ask_remove_key": True}, 400)
    _take_back(_zone(), api_id, f"You removed {row['name']}.")
    apis.remove(state, api_id)
    out = {"deleted": True, "yes": False, "line": f"Removed {row['name']}. Its Yes is taken back."}
    if row["key"] and remove_key:
        gone, why = _remove_key(row["name"], row["key"])
        if gone is None:
            said = f"{why} Until then its key stays in your vault, and an API added later never uses it."
            return _no_store(dict(out, key_removed=False, error=said, line=f"{out['line']} {said}"), 423)
        out["key_removed"] = gone
        out["line"] += " Its key is out of your vault."
    elif row["key"]:
        out["key_removed"] = False
        out["line"] += " Its key stays in your vault."
    return _no_store(out)


@built("PUT", "/v1/toolsmith/apis/<api_id>/key", OWNER_DEVICE)
def api_key_put(api_id: str):
    from ...zone1.vault import VaultError
    row = apis.rows(_state_dir()).get(api_id)
    if row is None:
        return _no_store({"error": NO_ROW_LINE}, 404)
    name = row["name"]
    if not row["key"]:
        return _no_store({"error": f"{name} takes no key. Say where its key goes first."}, 409)
    value = _body().get("value")
    value = value.strip() if isinstance(value, str) else ""
    if not web_key_ok(value):
        return _no_store({"error": f"That does not look like a {name} key. Copy the whole key and paste it on one "
                                   "line. Nothing was saved."}, 400)
    a = _archive()
    status = _vault_state(a)
    if status not in ("open", "locked"):                 # the wizard's own rule (Engine.keep_secret)
        return _no_store({"error": "Set up the vault first, so your key has a safe home." if status in ("", "not_set_up")
                          else "Your vault can't take a key right now. Open the Vault page to see why."}, 409)
    try:
        got = a.put_secret(row["key"], value, label="secret.api_key")
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code}, exc.status)
    finally:
        value = ""                                                   # noqa: F841 - not kept here
    apis.set_key_host(_state_dir(), api_id, row["host"])  # this key is for this host: it never follows the id elsewhere
    line = (f"Saved in your vault. Toolsmith uses {name} only after your Yes." if got.get("saved") else
            f"Sealed in your vault's inbox. It is filed the next time you unlock. Toolsmith uses {name} only after "
            "your Yes.")
    return _no_store({"saved": bool(got.get("saved")), "line": line, **_ask(_zone(), row)},
                     200 if got.get("saved") else 202)


@built("DELETE", "/v1/toolsmith/apis/<api_id>/key", OWNER_DEVICE)
def api_key_delete(api_id: str):
    """Also for a row already removed whose key she kept: the vault name comes from the id, in its own space."""
    if not apis.ID_RE.fullmatch(api_id):
        return _no_store({"error": NO_ROW_LINE}, 404)
    row = apis.rows(_state_dir()).get(api_id)
    name = row["name"] if row else "That API's"
    took_back = _take_back(_zone(), api_id, f"You removed the key for {name}.") if row else False
    gone, why = _remove_key(name, apis.key_name(api_id))
    if gone is None:
        return _no_store({"error": why, "yes": False}, 423)
    if row:
        apis.set_key_host(_state_dir(), api_id)
    if not gone and not took_back:
        return _no_store({"error": "No key is kept for it, so there is nothing to remove."}, 404)
    return _no_store({"deleted": True, "yes": False,
                      "line": f"Removed. The key for {name} is out of your vault and its Yes is taken back. You can also "
                              "delete the key where you got it."})


@built("POST", "/v1/toolsmith/apis/<api_id>/test", OWNER_DEVICE)
def api_test(api_id: str):
    from .toolsmith import service
    try:
        return _no_store(service().test_owner_api(api_id, _body().get("word"), by=_by()))
    except KeyError:
        return _no_store({"error": NO_ROW_LINE}, 404)
    except Paused as exc:                          # safe mode: Toolsmith is paused until restart
        return _no_store({"ok": False, "line": str(exc), "rows": [], "safe_mode": True}, 409)
