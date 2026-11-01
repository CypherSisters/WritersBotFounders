r"""Core API group `you` (lane I12): who the bot is, and what the owner tells it about herself.

Owner device only (a paired device: the owner at a screen, never a bare API key).

- ``GET /v1/settings/persona``: the persona and how the chat uses it (``zone2.persona.view``).
- ``PUT /v1/settings/persona`` ``{text}``: her own words; at most 4000 characters of plain text.
- ``POST /v1/settings/persona/redo``: written again from the goal, always on the brain on this computer (never a cloud company).
- ``GET /v1/settings/you``: ``{name, call_me, about, timezone}`` (empty strings until she answers).
- ``PUT /v1/settings/you``: the same four; kept in the vault (``puffpai_core.you``).
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ... import jsonfile, you
from ...zone2 import persona
from ..auth import OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("you", __name__)


def _body() -> object:
    return request.get_json(silent=True)


@route(bp, "GET", "/v1/settings/persona", access=OWNER_DEVICE)
def persona_get():
    return jsonify(persona.view(current_core().state))


@route(bp, "PUT", "/v1/settings/persona", access=OWNER_DEVICE)
def persona_put():
    body = _body()
    if not isinstance(body, dict) or "text" not in body:
        return jsonify(error='Send {"text": "..."}: who your bot is, in your words.'), 400
    core = current_core()
    try:
        persona.set_text(core.state, body["text"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(persona.view(core.state))


@route(bp, "POST", "/v1/settings/persona/redo", access=OWNER_DEVICE)
def persona_redo():
    core = current_core()
    ts = getattr(core, "toolsmith", None)
    build = persona.load(core.state).get("build") or \
        jsonfile.read(core.state / "settings" / "wizard.json", {}).get("build")
    if ts is None or not callable(getattr(ts, "write_persona", None)):
        return jsonify(error="Toolsmith is not part of this Core, so who your bot is can't be written again."), 409
    if not build:
        return jsonify(error="There is no goal yet. Answer the goal step in Setup first."), 409
    got = ts.write_persona(str(build), again=True)
    if not got.get("text"):
        return jsonify(error=got.get("said") or persona.NOT_WRITTEN), 409
    return jsonify(persona.view(core.state))


def _you_error(exc: you.YouError):
    return jsonify(error=str(exc), code="locked" if isinstance(exc, you.YouLocked) else "bad"), exc.status


@route(bp, "GET", "/v1/settings/you", access=OWNER_DEVICE)
def you_get():
    core = current_core()
    try:
        return jsonify(you.view(core.state, bus=core.bus))
    except you.YouError as exc:
        return _you_error(exc)


@route(bp, "PUT", "/v1/settings/you", access=OWNER_DEVICE)
def you_put():
    core = current_core()
    try:
        return jsonify(you.save(core.state, _body(), bus=core.bus))
    except you.YouError as exc:
        return _you_error(exc)
