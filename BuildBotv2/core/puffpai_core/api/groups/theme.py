"""Core API group `theme` (lane L5): look tokens so package pages can match the Desktop."""

from flask import Blueprint, jsonify

from ... import desktop
from ..auth import PACKAGE_OK
from ..routing import current_core, route

bp = Blueprint("theme", __name__)


@route(bp, "GET", "/v1/theme", access=PACKAGE_OK)
def theme_get():
    core = current_core()
    return jsonify(desktop.theme(core.state, core.registry))
