"""Core API group `consent` (lanes L1 and L4). Yes cards, the owner's answers, and revoke.

One store for the Core (:mod:`...consent`). ``GET /v1/consent?status=pending``:
the cards (what each one shows, and its answer), each with ``title`` and ``lines``
(``consent.card_view``: the card in plain words, from what ``card_sha`` covers). ``POST /v1/consent/<id>/answer``
``{"answer": "yes" | "no", "factor": "tap" | "pin", "pin": "...", "card_sha": "..."}`` (a Yes without
``card_sha`` is refused, 400):
a paired device only (an owner at a screen, never a bare API key or a package
token); the Core's Origin and ``X-PuffPAI`` check for cookie callers is the
app's own gate (lane L5), not repeated here. A floor-level card refuses a tap
and needs the PIN (D29). The PIN is never stored, logged or echoed. On the home network (D130), a card in
``DELETE /v1/consent/<id>``: take a Yes back.
"""

from flask import Blueprint, current_app, g, jsonify, request

from ...consent import STATUSES, ConsentError, card_view
from ...zone3 import addresses
from ...zone3.service import for_app
from ..auth import OWNER, OWNER_DEVICE
from ..routing import route

bp = Blueprint("consent", __name__)
_SHOWN = ("consent_id", "scope", "floor", "card", "card_sha", "status", "factor", "by", "created_at",
          "answered_at", "covers", "bound", "expires")


def _shown(rec: dict) -> dict:
    """The record, plus ``title`` and ``lines``: the card in plain words, made only from the stored card
    (what ``card_sha`` covers), so the Desktop shows exactly what the Yes grants."""
    out = {k: rec.get(k) for k in _SHOWN}
    seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor", True)))
    out["title"], out["lines"] = seen["title"], seen["lines"]
    return out


@route(bp, "GET", "/v1/consent", OWNER)
def consent_list():
    status = request.args.get("status") or None
    if status is not None and status not in STATUSES:
        return jsonify(error=f"status is one of {', '.join(STATUSES)}."), 400
    return jsonify([_shown(r) for r in for_app(current_app).consents.list(status=status)])


@route(bp, "DELETE", "/v1/consent/<id>", OWNER)
def consent_revoke(id):
    z = for_app(current_app)
    rec = z.consents.get(id)
    if not z.consents.revoke(id):
        return jsonify(error="No such Yes, or it was already taken back."), 404
    package = addresses.package_of(rec or {})
    addresses.drop_grant(z.bridge.drop_consent, id, package)      # one action, from here too: its grant goes with the Yes (D110)
    return jsonify(revoked=id)


@route(bp, "POST", "/v1/consent/<id>/answer", OWNER_DEVICE)
def consent_answer(id):
    body = request.get_json(silent=True) or {}
    caller = getattr(g, "caller", None)
    try:
        rec = for_app(current_app).consents.answer(
            id, str(body.get("answer") or ""), str(body.get("factor") or "tap"),
            pin=body.get("pin") if isinstance(body.get("pin"), str) else None,
            by=str(getattr(caller, "label", "") or "owner"),
            card_sha=body.get("card_sha") if isinstance(body.get("card_sha"), str) else None)
    except ConsentError as exc:
        return jsonify(error=exc.message, code=exc.code), exc.status
    return jsonify(dict(_shown(rec), answer=rec.get("answer")))
