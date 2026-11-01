"""Core API group `registry` (lane L0, built): everything installed.

``GET /v1/registry`` (owner) lists every row, optionally filtered by ``kind``
and ``status``. ``GET /v1/registry/<id>`` (owner, or a package token for its
own row) returns one row. Rows follow ``contracts/registry.schema.json``.
"""

from flask import Blueprint, jsonify, request

from ..auth import OWN_ROW, OWNER
from ..routing import current_core, route

bp = Blueprint("registry", __name__)


@route(bp, "GET", "/v1/registry", access=OWNER)
def registry_list():
    from ... import desktop
    from ...zone2.apps import puffpai_own
    core = current_core()
    rows = core.registry.list(kind=request.args.get("kind") or None, status=request.args.get("status") or None)
    for row in rows:                        # not stored: whether it runs a process (a stopped page reads "ready")
        row["process"] = desktop.has_process(core.state, row["id"], core.registry)
        row["own"] = puffpai_own(row)       # not stored either: may its page show inside (D83); the Desktop asks here
    return jsonify(rows)


@route(bp, "GET", "/v1/registry/<id>", access=OWN_ROW)
def registry_get(id):
    row = current_core().registry.get(id)
    if row is None:
        return jsonify(error=f"Nothing installed with id {id!r}."), 404
    return jsonify(row)
