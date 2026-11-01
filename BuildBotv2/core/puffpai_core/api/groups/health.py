"""Core API group `health` (lane L0, built): is the Core up. Public, says nothing private.

``GET /v1/core`` (lane I22b, D91): which bot this Core runs for, for the owner's own programs. ``puffpai`` asks it
with the terminal key before it counts a Core as running: a stale ``terminal.json`` can point at a port another
bot's Core holds now.
"""

import os

from flask import Blueprint, jsonify

from ... import __version__
from ...corelock import bot_id
from ..auth import OWNER, PUBLIC
from ..routing import current_core, route

bp = Blueprint("health", __name__)


@route(bp, "GET", "/v1/health", access=PUBLIC)
def health():
    return jsonify(ok=True, version=__version__, api="v1")


@route(bp, "GET", "/v1/core", access=OWNER)
def core_identity():
    return jsonify(bot_id=bot_id(current_core().state), pid=os.getpid())
