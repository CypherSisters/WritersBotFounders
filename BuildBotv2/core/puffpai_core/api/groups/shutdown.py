"""Core API group `shutdown` (lane I7): the owner stops BuildBot from another window (``puffpai shutdown``,
``Stop BuildBot.cmd``).

Owner at this computer only (a paired-device token, as the terminal key in ``settings\\terminal.json`` is): an
owner API key, a package token and a Toolsmith token are refused (``auth.decide``). The route only asks: the
process that runs the Core (``serve`` or ``setup``) sets ``puffpai.stop``, and its own stop then ends Toolsmith and
every app through their Job Objects, the same stop as Ctrl+C. The route never refuses because setup is running
(D122): the setup window sees the stop at its next prompt or poll.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify

from ..auth import OWNER_DEVICE
from ..routing import route

bp = Blueprint("shutdown", __name__)

#: The extension the Core's own process sets (a ``threading.Event``), and watches, from before its listeners start.
STOP = "puffpai.stop"


@route(bp, "POST", "/v1/shutdown", access=OWNER_DEVICE)
def shutdown():
    from ..app import accept_shutdown
    stop = accept_shutdown(current_app)
    caller = getattr(g, "caller", None)
    current_app.logger.info("shutdown asked by %s", getattr(caller, "label", "the owner"))
    stop.set()
    return jsonify(stopping=True, said="BuildBot is stopping its apps."), 202
