"""Core API group `notify` (lane L5): a package asks the Core to tell the owner something.

``POST /v1/notify`` (owner or a package token; the notice is filed under the
caller's package, "core" for an owner), ``GET /v1/notify`` (owner: the
notices not dismissed), ``DELETE /v1/notify/<id>`` (owner: dismiss).
"""

from flask import Blueprint, current_app, g, jsonify, request

from ...notices import NoticeError, Notices
from ..auth import OWNER, PACKAGE_OK
from ..routing import route

bp = Blueprint("notify", __name__)


def init_app(app) -> None:
    core = app.extensions["puffpai"]
    app.extensions["puffpai.notices"] = Notices(core.state / "notices.json", bus=core.bus)


def _notices() -> Notices:
    return current_app.extensions["puffpai.notices"]


@route(bp, "POST", "/v1/notify", access=PACKAGE_OK)
def notify_post():
    package = g.caller.package_id if g.caller.kind == "package" else "core"
    try:
        notice = _notices().post(package, request.get_json(silent=True))
    except NoticeError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(id=notice["id"]), 201


@route(bp, "GET", "/v1/notify", access=OWNER)
def notify_list():
    return jsonify(_notices().list())


@route(bp, "DELETE", "/v1/notify/<id>", access=OWNER)
def notify_dismiss(id):
    if not _notices().dismiss(id):
        return jsonify(error=f"No notice with id {id!r}."), 404
    return jsonify(ok=True)
