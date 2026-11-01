# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/programs_api.py (adapted): the same routes under /v1, the Developer
# routes' own guard.
"""Core API group `programs` (D16, D138): her own programs, from the Developer page. PROPOSED, not tested by the owner.

``GET /v1/programs``, ``POST /v1/programs``, ``DELETE`` and ``PATCH /v1/programs/<id>``, ``POST
/v1/programs/<id>/launch``. Only the owner at a paired device (the same guard as the Developer shell: never an owner API
key, a package token, a Toolsmith token or the terminal key the Core writes for its own CLI), because Always allow lets
the brain run a program with no Yes. Adding, turning on and opening are refused in safe mode; listing, removing and
turning off always work (they only narrow). Every change writes an audit line first, with names only (X14); no line, no
change, except a change that only narrows. The logic: ``zone2.programs``.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...zone2 import programs
from ..auth import OWNER_DEVICE
from ..routing import current_core, route
from .dev import _refused as _not_her

bp = Blueprint("programs", __name__)

NO_AUDIT = "BuildBot could not write the audit line, so nothing was {done}."


def _state():
    return current_core().state


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _audit(action: str, **fields) -> None:
    from ...zone3.service import for_app
    for_app(current_app).audit.append({"actor": "owner", "action": action, **fields})


def _no_such():
    return jsonify(error=programs.NO_SUCH), 404


@route(bp, "GET", "/v1/programs", access=OWNER_DEVICE)
def programs_list():
    refused = _not_her(safe_too=False)
    if refused:
        return refused
    return jsonify(programs=programs.rows(_state()), installed_desktop=programs.installed_desktop())


@route(bp, "POST", "/v1/programs", access=OWNER_DEVICE)
def programs_add():
    refused = _not_her()
    if refused:
        return refused
    body = _body()
    try:
        _audit("program_added", kind=str(body.get("kind") or "")[:10])
    except Exception:  # noqa: BLE001 - not logged: not added
        return jsonify(error=NO_AUDIT.format(done="added")), 503
    try:
        row = programs.register(_state(), str(body.get("kind") or ""), path=str(body.get("path") or ""),
                                command=str(body.get("command") or ""), name=str(body.get("name") or ""))
    except programs.ProgramError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, program=row)


@route(bp, "DELETE", "/v1/programs/<id>", access=OWNER_DEVICE)
def programs_remove(id):
    refused = _not_her(safe_too=False)
    if refused:
        return refused
    if not programs.ID_RE.match(str(id)) or not programs.remove(_state(), id):
        return _no_such()
    try:
        _audit("program_removed", program=id)
    except Exception:  # noqa: BLE001 - removing only narrows: it stands either way
        pass
    return jsonify(ok=True)


@route(bp, "PATCH", "/v1/programs/<id>", access=OWNER_DEVICE)
def programs_change(id):
    body = _body()
    on = body.get("always_allow")
    refused = _not_her(safe_too=on is not False)
    if refused:
        return refused
    if not programs.ID_RE.match(str(id)) or programs.get(_state(), id) is None:
        return _no_such()
    if not isinstance(on, bool):
        return jsonify(error="Say on or off for Always allow."), 400
    try:
        _audit("program_always_allow", program=id, on=on)
    except Exception:  # noqa: BLE001 - turning on with no line: no. Turning off only narrows.
        if on:
            return jsonify(error=NO_AUDIT.format(done="changed")), 503
    try:
        return jsonify(ok=True, **programs.set_always_allow(_state(), id, on))
    except programs.ProgramError as exc:
        return jsonify(error=str(exc)), 400


@route(bp, "POST", "/v1/programs/<id>/launch", access=OWNER_DEVICE)
def programs_launch(id):
    refused = _not_her()
    if refused:
        return refused
    if not programs.ID_RE.match(str(id)) or programs.get(_state(), id) is None:
        return _no_such()
    try:
        _audit("own_program_opened", program=id)
    except Exception:  # noqa: BLE001 - not logged: not started
        return jsonify(error=NO_AUDIT.format(done="started")), 503
    try:
        return jsonify(programs.launch(_state(), id, str(_body().get("file") or "")))
    except programs.ProgramError as exc:
        return jsonify(error=str(exc)), 400
    except OSError as exc:
        return jsonify(error=f"It did not start ({type(exc).__name__})."), 500
