"""Core API group `providers` (lane L1, built). Which provider fills each zone slot.

``GET /v1/providers``: every slot, its installed providers and the active one.
``PUT /v1/providers/<slot>`` ``{"id"}``: pick the active provider of a
one-at-a-time slot; raises a Yes card (202 with its ``consent_id``).
"""

from flask import Blueprint, jsonify, request

from ...packages.manager import AddError
from ..auth import OWNER
from ..routing import current_core, route

bp = Blueprint("providers", __name__)


@route(bp, "GET", "/v1/providers", access=OWNER)
def providers_list():
    return jsonify(current_core().providers.list())


@route(bp, "PUT", "/v1/providers/<slot>", access=OWNER)
def providers_pick(slot):
    body = request.get_json(silent=True) or {}
    if not isinstance(body.get("id"), str):
        return jsonify(error='Send {"id": "<package id>"}.'), 400
    try:
        return jsonify(current_core().providers.pick(slot, body["id"])), 202
    except AddError as exc:
        return jsonify(error=exc.problems[0], problems=exc.problems), exc.status
