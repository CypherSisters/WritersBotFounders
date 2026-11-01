"""Core API group ``github`` (lane I18g): link the owner's GitHub key, add code from GitHub for a build, and link
her own web search keys.

The Desktop shows one GitHub block in two places (Developer and Apps > Add); these are its routes.

- ``GET /v1/github/key``: linked or not (``linked`` true, false, or null when the vault is locked or not set up,
  so it can't be told), with the vault's state and one plain line. Never a value.
- ``PUT /v1/github/key`` ``{value}``: the key must have a token's strict shape (``github_key_ok``, the same check
  Toolsmith makes before it ever sends one), then it is kept in the vault as ``toolsmith.github`` through the
  Archivist, as ``PUT /v1/toolsmith/keys/<provider>`` keeps a cloud key: vault open, saved (200); locked, sealed
  into the vault inbox (202); not set up, refused (409).
- ``DELETE /v1/github/key``: takes it out (the vault must be open).
- ``POST /v1/github/code`` ``{address}``: plans a new app from that one GitHub project (any common form of its
  address), on the same path as ``POST /v1/toolsmith/plan``: its plan card and the ONE Yes.
  ``{address, job_id}``: a job of an earlier build that is held or found nothing gets a one-job plan with that
  address as its only try (03 3.2 rule 6). Always a NEW plan with its own card: no Yes given before is reused.

A saved key is lent only by Toolsmith's own GitHub search and read of her project list (D135;
``ToolsmithHost._github_key``), through the Bridge,
to api.github.com only (planning searches included, under the Bridge choice to let Toolsmith search). Nothing here
sends anything to GitHub, and no route returns, echoes or logs a key.

Her own web search keys (``sources.json`` rows ``web``), the same way:

- ``GET /v1/toolsmith/search-key/<id>``: linked or not, and whether her Yes for it is live. Never a value.
- ``PUT /v1/toolsmith/search-key/<id>`` ``{value}``: the key must have a key's strict shape (``web_key_ok``), then it
  is kept in the vault as ``toolsmith.<id>`` as above, and its own Yes card is raised: kind ``host``, subject
  ``toolsmith:search``, exactly that row's one host, build phase. A key alone sends nothing.
- ``DELETE /v1/toolsmith/search-key/<id>``: her Yes for it is taken back, whatever else happens, and the key goes.

A row is on only with her Yes, the key, an open vault and Toolsmith's search on. Lent only per search while a plan is
made, on that row's one host, in its own header, never in an address (``ToolsmithHost._web_key``).
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from ...zone1 import archivist as _archivist
from ...zone3.toolsmith import errors
from ...zone3.toolsmith.find import github_of
from ...zone3.toolsmith_host import address as ts_address, store as ts_store
from ...zone3.toolsmith_host.service import GITHUB_KEY_NAME, Paused, github_key_ok, web_key_ok
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route
from .zones import _body, _key_state, _no_store

bp = Blueprint("github", __name__)
LANE = "I18g"
MAX_ADDRESS = 500
MAX_JOB_ID = 100

LINKED_LINE = ("Linked. Toolsmith adds your key only to its own GitHub searches and to reading your project list, "
               "and only on api.github.com.")
NOT_LINKED_LINE = "Not linked. Toolsmith searches GitHub as a visitor."
LOCKED_LINE = ("Your vault is locked, so BuildBot can't tell whether your GitHub key is kept. Searches go "
               "without it while it is locked.")
NO_VAULT_LINE = "No vault yet. Your GitHub key is kept in your vault, so set it up first."
BAD_SHAPE_LINE = ("That does not look like a GitHub token. Copy the whole token from GitHub and paste it on one "
                  "line. Nothing was saved.")
NOT_GITHUB_LINE = "That is not a GitHub project address. Paste one like https://github.com/owner/project."
JOB_NOT_OPEN_LINE = ("That job has a working app, or Toolsmith is still on it, so there is nothing to add code "
                     "to. Add it as a new app instead.")
JOB_LIBRARY_LINE = ("That job has no app yet, but it has ended, so there is nothing to add code to. Add it as a new "
                    "app instead.")
#: What each web search key means for her, said on its card and in its block.
WEB_KEY_SAYS = {"brave": "Brave has no free plan: it charges you for each search, on your own Brave account.",
                "marginalia": "Marginalia gives its free key by email, so asking for one shares your address with "
                              "them. That is your choice."}


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _archive():
    core = current_core()
    return _archivist.for_state(core.state, core.bus)


def _vault_state(a) -> str:
    try:
        return str(a.vault.status().get("state") or "")
    except Exception:  # noqa: BLE001 - can't tell
        return ""


# -- linking her GitHub key ------------------------------------------------------------------------------

@built("GET", "/v1/github/key", OWNER)
def github_key_get():
    a = _archive()
    vault = _vault_state(a)
    linked = _key_state(a, GITHUB_KEY_NAME)
    line = (LINKED_LINE if linked is True else NOT_LINKED_LINE if linked is False
            else NO_VAULT_LINE if vault in ("", "not_set_up") else LOCKED_LINE)
    return _no_store({"linked": linked, "vault": vault or "not_set_up", "vault_name": GITHUB_KEY_NAME, "line": line})


@built("PUT", "/v1/github/key", OWNER_DEVICE)
def github_key_put():
    from ...zone1.vault import VaultError
    value = _body().get("value")
    value = value.strip() if isinstance(value, str) else ""
    if not github_key_ok(value):
        return _no_store({"error": BAD_SHAPE_LINE}, 400)
    a = _archive()
    status = _vault_state(a)
    if status not in ("open", "locked"):                 # the wizard's own rule (Engine.keep_secret)
        return _no_store({"error": "Set up the vault first, so your key has a safe home." if status in ("", "not_set_up")
                          else "Your vault can't take a key right now. Open the Vault page to see why."}, 409)
    try:
        got = a.put_secret(GITHUB_KEY_NAME, value, label="secret.api_key")
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code}, exc.status)
    finally:
        value = ""                                                   # noqa: F841 - not kept here
    line = ("Linked. Your GitHub key is saved in your vault." if got.get("saved") else
            "Sealed in your vault's inbox. It is filed the next time you unlock.")
    return _no_store({"saved": bool(got.get("saved")), "line": line}, 200 if got.get("saved") else 202)


@built("DELETE", "/v1/github/key", OWNER_DEVICE)
def github_key_delete():
    from ...zone1.vault import VaultError
    a = _archive()
    if not a.is_open():
        return _no_store({"error": "Unlock your vault first, then unlink GitHub."}, 423)
    try:
        gone = a.delete_secret(GITHUB_KEY_NAME)
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code}, exc.status)
    if not gone:
        return _no_store({"error": "No GitHub key is kept, so there is nothing to unlink."}, 404)
    return _no_store({"deleted": True, "line": "Unlinked. Your GitHub key is out of your vault. Toolsmith searches "
                                                "GitHub as a visitor. You can also delete the token on GitHub."})


# -- linking her own web search keys ---------------------------------------------------------------------

def _web_row(row_id: str) -> dict | None:
    return ts_address.key_rows().get(row_id)


def _key_cards(z, host: str, status: str) -> list[dict]:
    """This key's own Yes cards in *status*: kind host, Toolsmith's search, exactly its one host."""
    from ...zone3.bridge import is_key_yes
    from .zones import _search_cards
    # An API she added on this same host has its own Yes (``covers.owner_api``): it never counts for this key.
    return [r for r in _search_cards(z, status) if is_key_yes(r) and (r.get("covers") or {}).get("hosts") == [host]
            and not (r.get("covers") or {}).get("owner_api")]


def _key_card(row: dict, row_id: str) -> dict:
    name, host = row.get("name") or row_id, row["host"]
    return {"title": f"Let Toolsmith use your own {name} key while it builds?",
            "sentence": f"While Toolsmith makes a plan, it may send search words like \"world news map\" to {host}, "
                        f"with your {name} key. Only search words go out, nothing else.",
            "lines": [WEB_KEY_SAYS.get(row_id, ""),
                      "Your key stays in your vault. It goes only to that one place, only for those searches.",
                      "It works only while Toolsmith's search is on. Remove the key to take this Yes back."]}


@built("GET", "/v1/toolsmith/search-key/<row_id>", OWNER)
def search_key_get(row_id: str):
    from ...zone3.service import for_app
    row = _web_row(row_id)
    if row is None:
        return _no_store({"error": "There is no such search place."}, 404)
    a, name = _archive(), row.get("name") or row_id
    vault = _vault_state(a)
    linked = _key_state(a, row["key"])
    yes = bool(_key_cards(for_app(current_app), row["host"], "granted"))
    line = ((f"Linked. Toolsmith adds your key only to its own {name} searches, only on {row['host']}." if yes else
             f"Linked, but waiting for your Yes, so {name} stays off. Link the key again to see its card.")
            if linked is True else f"Not linked. Toolsmith does not use {name}." if linked is False
            else "No vault yet. Your key is kept in your vault, so set it up first." if vault in ("", "not_set_up")
            else f"Your vault is locked, so BuildBot can't tell whether your key is kept. {name} stays off while it "
                 "is locked.")
    return _no_store({"linked": linked, "yes": yes, "vault": vault or "not_set_up", "vault_name": row["key"],
                      "host": row["host"], "says": WEB_KEY_SAYS.get(row_id, ""), "line": line})


@built("PUT", "/v1/toolsmith/search-key/<row_id>", OWNER_DEVICE)
def search_key_put(row_id: str):
    from ...consent import ConsentError, card_view
    from ...wiring import WizardBridge
    from ...zone1.vault import VaultError
    from ...zone3.service import for_app
    row = _web_row(row_id)
    if row is None:
        return _no_store({"error": "There is no such search place."}, 404)
    name = row.get("name") or row_id
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
    line = (f"Saved in your vault. Toolsmith uses {name} only after your Yes." if got.get("saved") else
            f"Sealed in your vault's inbox. It is filed the next time you unlock. Toolsmith uses {name} only after "
            "your Yes.")
    out = {"saved": bool(got.get("saved")), "line": line}
    code = 200 if got.get("saved") else 202
    z = for_app(current_app)
    if _key_cards(z, row["host"], "granted"):
        return _no_store(dict(out, yes=True), code)          # her Yes for this place stands: no second card
    waiting = _key_cards(z, row["host"], "pending")
    try:
        rec = waiting[-1] if waiting else z.consents.ask(
            "host", WizardBridge.SEARCH, {"hosts": [row["host"]], "phase": "build", "card": _key_card(row, row_id)})
    except ConsentError as exc:
        return _no_store(dict(out, yes=False, error=str(getattr(exc, "message", "") or exc)), code)
    seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor")))
    return _no_store(dict(out, yes=False, consent_id=rec["consent_id"], card_sha=rec.get("card_sha"),
                          title=seen["title"], lines=seen["lines"]), code)


@built("DELETE", "/v1/toolsmith/search-key/<row_id>", OWNER_DEVICE)
def search_key_delete(row_id: str):
    from ...zone1.vault import VaultError
    from ...zone3.service import for_app
    row = _web_row(row_id)
    if row is None:
        return _no_store({"error": "There is no such search place."}, 404)
    name = row.get("name") or row_id
    z = for_app(current_app)
    took_back = False
    for rec in _key_cards(z, row["host"], "pending"):
        took_back = z.consents.expire(rec["consent_id"], f"You removed your {name} key.") or took_back
    for rec in _key_cards(z, row["host"], "granted"):
        took_back = z.consents.revoke(rec["consent_id"]) or took_back
    a = _archive()
    if not a.is_open():                                  # her Yes is gone already: the key waits for the vault
        return _no_store({"error": f"Toolsmith no longer uses {name}. Unlock your vault to take the key out too.",
                          "yes": False}, 423)
    try:
        gone = a.delete_secret(row["key"])
    except VaultError as exc:
        return _no_store({"error": exc.message, "code": exc.code, "yes": False}, exc.status)
    if not gone and not took_back:
        return _no_store({"error": f"No {name} key is kept, so there is nothing to remove."}, 404)
    return _no_store({"deleted": True, "yes": False,
                      "line": f"Removed. Your {name} key is out of your vault and its Yes is taken back. You can also "
                              f"delete the key at {name}."})


# -- adding code from GitHub for the build -----------------------------------------------------------------

@built("POST", "/v1/github/code", OWNER)
def github_code():
    from .toolsmith import service
    body = _body()
    raw = body.get("address")
    full = github_of(raw) if isinstance(raw, str) and len(raw) <= MAX_ADDRESS else ""
    if not full:
        return jsonify(error=NOT_GITHUB_LINE), 400
    url = f"https://github.com/{full}"
    repo = full.split("/", 1)[1]
    job_id = body.get("job_id")
    host = service()
    try:
        if job_id in (None, ""):
            got = host.plan(f"Add {repo} from GitHub",
                            [{"name": repo[:80], "does": f"Run {repo}, the project at {url}", "pasted": [url]}])
            return jsonify(got), 201
        if not isinstance(job_id, str) or len(job_id) > MAX_JOB_ID:
            return jsonify(error="Send the job's id as it is on Building."), 400
        try:
            old = host.view(job_id.rsplit("-", 1)[0])
        except KeyError:
            return jsonify(error="There is no such job."), 404
        j = next((x for x in old.get("jobs") or [] if x.get("id") == job_id), None)
        if j is None:
            return jsonify(error="There is no such job."), 404
        if old.get("replaced_by") or j.get("status") not in ts_store.UNFINISHED:
            return jsonify(error=JOB_LIBRARY_LINE if j.get("library") else JOB_NOT_OPEN_LINE), 409
        job = {"name": j["name"], "does": j.get("does") or j["name"], "sample_ask": j.get("sample_ask") or "",
               "pasted": [url]}
        got = host.plan(f"{j['name']}, from github.com/{full}", [job])   # a new plan: its own card, its own Yes
        return jsonify(dict(got, for_job={"build": old["id"], "job": job_id, "name": j["name"]})), 201
    except Paused as exc:                          # safe mode: Toolsmith is paused until restart
        return jsonify(dict(errors.to_wire(exc), safe_mode=True)), 409
