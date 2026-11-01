"""Core API group `vault` (lane L3). Owner vault routes (zones/Z1_VAULT_PROVIDERS.md section 8).

No route ever returns a secret value or key bytes. The only secrets that travel
out are the recovery code at setup, rotate and recover: once, to the owner at a
paired device, with ``Cache-Control: no-store``. Typed secrets (PIN, recovery
code, a key) are never echoed back, in replies or in error text (D36(c)).
Package tokens can call none of these (``owner`` / ``owner_device`` access).

Not built yet (501, lane L3): store/move and backup/*. ``hold`` is Core only.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from ..auth import CORE, OWNER, OWNER_DEVICE
from ..routing import current_core, route, stub
from ...zone1 import archivist as _archivist
from ...zone1.vault import VaultError

bp = Blueprint("vault", __name__)
LANE = "L3"
_KEY_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def built(method: str, rule: str, access: str) -> Callable:
    """``route`` plus the lane that built it (the contract test reads ``puffpai_built_by``)."""
    def deco(fn: Callable) -> Callable:
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _arch():
    core = current_core()
    return _archivist.for_state(core.state, core.bus)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _reply(payload: Any, status: int = 200):
    r = jsonify(payload)
    r.status_code = status
    r.headers["Cache-Control"] = "no-store"
    return r


def _error(e: VaultError):
    out = {"error": e.message, "code": e.code}
    if hasattr(e, "retry_after"):
        out["retry_after"] = int(e.retry_after) + 1
    r = _reply(out, e.status)
    if hasattr(e, "retry_after"):
        r.headers["Retry-After"] = str(out["retry_after"])
    return r


def _guard(fn: Callable) -> Callable:
    def wrapper(**kw):
        try:
            return fn(**kw)
        except VaultError as e:
            return _error(e)
    wrapper.__name__ = fn.__name__
    return wrapper


def _text(body: dict, key: str) -> str:
    v = body.get(key)
    return v if isinstance(v, str) else ""


@built("GET", "/v1/vault/status", OWNER)
@_guard
def vault_status():
    a = _arch()
    out = a.vault.status()
    out["inbox_waiting"] = a.inbox.pending()
    out["inbox_conflicts"] = list(a.inbox_conflicts)       # key names only: boxes waiting for her PIN Yes
    out["leases"] = len(a.leases())
    return _reply(out)


@built("POST", "/v1/vault/setup", OWNER_DEVICE)
@_guard
def vault_setup():
    code = _arch().setup(_text(_body(), "pin"))
    return _reply({"state": "open", "recovery_code": code,
                   "note": "Write this code down now. It is shown once. With it you can always get back in."}, 201)


@built("POST", "/v1/vault/unlock", OWNER_DEVICE)
@_guard
def vault_unlock():
    body = _body()
    _arch().unlock(_text(body, "method") or "pin", _text(body, "secret"), accept_older=body.get("accept_older") is True)
    return _reply({"state": "open"})


@built("POST", "/v1/vault/lock", OWNER)
@_guard
def vault_lock():
    _arch().lock("owner")
    return _reply({"state": "locked"})


@built("POST", "/v1/vault/recover", OWNER_DEVICE)
@_guard
def vault_recover():
    body = _body()
    code = _arch().vault.recover(_text(body, "code"), _text(body, "new_pin"))
    return _reply({"state": "open", "recovery_code": code,
                   "note": "Your old recovery code and old backups no longer open the vault. Write this new code down."})


@built("POST", "/v1/vault/pin", OWNER_DEVICE)
@_guard
def vault_pin():
    body = _body()
    _arch().vault.change_pin(_text(body, "pin"), _text(body, "new_pin"))
    return _reply({"ok": True})


@built("POST", "/v1/vault/recovery-code/rotate", OWNER_DEVICE)
@_guard
def vault_recovery_rotate():
    code = _arch().vault.rotate_recovery(_text(_body(), "pin"))
    return _reply({"recovery_code": code, "note": "Your old recovery code no longer works. Write this one down."})


@built("POST", "/v1/vault/recovery-code/test", OWNER_DEVICE)
@_guard
def vault_recovery_test():
    ok = _arch().vault.test_recovery(_text(_body(), "code"))
    return _reply({"ok": ok})


@built("GET", "/v1/vault/ways-in", OWNER)
@_guard
def vault_ways_in():
    return _reply({"ways_in": _arch().vault.ways_in()})


@built("POST", "/v1/vault/ways-in", OWNER_DEVICE)
@_guard
def vault_ways_in_add():
    """Floor-level (D29): the current PIN or the recovery code, as well as the new secret."""
    body = _body()
    way = _arch().vault.add_way(_text(body, "kind") or "passphrase", _text(body, "label"), _text(body, "secret"),
                                pin=_text(body, "pin"), code=_text(body, "code"))
    return _reply(way, 201)


@built("DELETE", "/v1/vault/ways-in/<id>", OWNER_DEVICE)
@_guard
def vault_ways_in_remove(id: str):
    """Floor-level (D29): the current PIN or the recovery code in the body."""
    body = _body()
    _arch().vault.remove_way(id, pin=_text(body, "pin"), code=_text(body, "code"))
    return _reply({"ok": True})


@built("POST", "/v1/vault/inbox/file", OWNER_DEVICE)
@_guard
def vault_inbox_file():
    """Her PIN Yes (D29) to let a waiting box replace the key saved under that name."""
    body = _body()
    name = _text(body, "name")
    if not _KEY_NAME.match(name):
        return _reply({"error": "A key name is capital letters, digits and _, starting with a letter."}, 400)
    return _reply({"replaced": _arch().file_replacing(name, _text(body, "pin"))})


@built("PUT", "/v1/vault/secrets/<name>", OWNER)
@_guard
def vault_secret_put(name: str):
    if not _KEY_NAME.match(name):
        return _reply({"error": "A key name is capital letters, digits and _, starting with a letter."}, 400)
    body = _body()
    value = _text(body, "value")
    if not value:
        return _reply({"error": "Nothing to save."}, 400)
    a = _arch()
    if not a.is_open() and body.get("seal_if_locked") is not True:
        return _reply({"error": "Your vault is locked. Unlock it with your PIN, or seal this into the vault "
                                "inbox to be filed when you unlock.", "code": "locked", "can_seal": True}, 423)
    out = a.put_secret(name, value, label=_text(body, "label") or "secret.api_key",
                       unattended=body.get("unattended") is True, card_backed=body.get("card_backed") is True)
    return _reply(out, 201 if out["saved"] else 202)


@built("DELETE", "/v1/vault/secrets/<name>", OWNER)
@_guard
def vault_secret_delete(name: str):
    if not _KEY_NAME.match(name):
        return _reply({"error": "A key name is capital letters, digits and _, starting with a letter."}, 400)
    _arch().delete_secret(name)
    return _reply({"ok": True})


stub(bp, "POST", "/v1/vault/store/move", OWNER_DEVICE, "L3")
stub(bp, "POST", "/v1/vault/backup/now", OWNER, "L3")
stub(bp, "POST", "/v1/vault/backup/list", OWNER, "L3")
stub(bp, "POST", "/v1/vault/backup/restore", OWNER_DEVICE, "L3")
stub(bp, "POST", "/v1/vault/backup/test", OWNER, "L3")
stub(bp, "POST", "/v1/vault/hold", CORE, "L3")
