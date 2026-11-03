"""Core API group `bridge` (lane L4). Open or closed, the per-host list, what went out.

Owner only. In safe mode, opening is floor-level: it needs the PIN (D29) and the refusal says the
safe-mode reason first (``Bridge.open_guard``, set by the Core's wiring). Nothing here grants a host: grants come only from the owner's Yes
on a card (``/v1/consent/<id>/answer``). Opening the Bridge opens only what
was granted (Z3 section 2). Packages, providers and Toolsmith have no route
that writes the policy (X24).
"""

from flask import Blueprint, current_app, jsonify, request

from ...zone3.bridge import OpenBusy, OpenRefused
from ...zone3.service import for_app
from ..auth import OWNER
from ..routing import route

bp = Blueprint("bridge", __name__)
HONEST = ("Native apps use the Bridge by cooperation: one caught going around it is stopped at once, but a "
          "short connection between checks can slip. Docker apps run behind the Docker wall.")


def _safe_mode() -> dict:
    """``{on, reasons}``: whether the Core is in safe mode (then opening needs the PIN). Can't tell = on."""
    core = current_app.extensions.get("puffpai")
    try:
        reasons = list(core.caretaker.safe_reasons) if core is not None else []
    except Exception:  # noqa: BLE001
        reasons = ["BuildBot could not tell whether it is in safe mode."]
    return {"on": bool(reasons), "reasons": reasons}


@route(bp, "GET", "/v1/bridge", OWNER)
def bridge_get():
    z = for_app(current_app)
    # The one owner-facing reading (Bridge.view): state, shown and said agree with /v1/settings and the CLI.
    return jsonify(dict(z.bridge.view(_names()), note=HONEST, cards=z.cards[-20:], safe_mode=_safe_mode()))


def _names() -> dict:
    core = current_app.extensions.get("puffpai")
    return {r["id"]: r["name"] for r in core.registry.list()} if core is not None else {}


@route(bp, "POST", "/v1/bridge/open", OWNER)
def bridge_open():
    """Open the Bridge. In safe mode it is floor-level: ``{"pin": ...}`` (D29), else 403 with the reason."""
    body = request.get_json(silent=True)
    pin = body.get("pin") if isinstance(body, dict) else None
    try:
        bridge = for_app(current_app).bridge
        bridge.open(by="owner", pin=pin if isinstance(pin, str) else None)
        return jsonify(bridge.view(_names()))
    except OpenBusy as exc:                    # no PIN would help, so no PIN card
        return jsonify(error=exc.line, code="bridge_busy"), exc.status
    except OpenRefused as exc:
        return jsonify(error=exc.line, code="safe_mode_pin", safe_mode={"on": True, "reasons": exc.reasons},
                       needs_pin=True), exc.status


@route(bp, "POST", "/v1/bridge/close", OWNER)
def bridge_close():
    bridge = for_app(current_app).bridge
    bridge.close(by="owner")
    return jsonify(bridge.view(_names()))


@route(bp, "GET", "/v1/bridge/ledger", OWNER)
def bridge_ledger():
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 2000))
    except ValueError:
        limit = 200
    return jsonify(for_app(current_app).bridge.ledger.read(limit))
