"""Core API group `audit` (lane L4). The audit log, read only, names and codes, never values.

The log is a hash chain written only by the Core (X14). No route writes it.
"""

from flask import Blueprint, current_app, jsonify, request

from ...zone3.chain import verify
from ...zone3.service import for_app
from ..auth import OWNER
from ..routing import route

bp = Blueprint("audit", __name__)


@route(bp, "GET", "/v1/audit", OWNER)
def audit_get():
    z = for_app(current_app)
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 2000))
    except ValueError:
        limit = 200
    ok, bad = verify(z.audit.path)
    return jsonify(chain_ok=ok, bad_line=bad, lines=z.audit.read(limit))
