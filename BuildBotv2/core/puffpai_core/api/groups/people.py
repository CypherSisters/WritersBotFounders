"""Core API group `people` (lane L5): the Core's shared people list (D12).

A package token may read it (02 "PUFFPAI_TOKEN", D48); only the owner may change it.
"""

from flask import Blueprint, jsonify, request

from ... import people
from ..auth import OWNER, PACKAGE_OK
from ..routing import current_core, route

bp = Blueprint("people", __name__)


@route(bp, "GET", "/v1/people", access=PACKAGE_OK)
def people_get():
    return jsonify(people.load(current_core().state))


@route(bp, "PUT", "/v1/people", access=OWNER)
def people_put():
    try:
        return jsonify(people.save(current_core().state, request.get_json(silent=True)))
    except people.PeopleError as exc:
        return jsonify(error=str(exc)), 400
