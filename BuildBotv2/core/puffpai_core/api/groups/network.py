"""Core API group `network` (D130, ``api.network``): BuildBot on her home network, and her paired browsers.

``GET /v1/network``: is it on, and the addresses to type. ``PUT /v1/network`` turns it off or on, at once, from any
paired device (off is one step from the terminal too, with BuildBot stopped). ``POST /v1/network/code``: a pairing code,
to type on another device. ``GET /v1/network/pages``, on the network only: each package's page port, with a page pass for
it (``auth.TokenStore.page_pass``).

``GET /v1/devices``, ``DELETE /v1/devices/<id>``: every paired browser, and removing one. It stops on its very next
request. A device on the network may do this too: it only narrows, and a lost phone needs it (as in v1).

All are ``owner_device``: this computer's terminal key and paired browsers, never an owner API key, a package token or
a Toolsmith token.
"""

from flask import Blueprint, current_app, g, jsonify, request

from ... import pairing
from .. import auth
from ..auth import OWNER_DEVICE
from ..network import EXTENSION, NETWORK_LISTENER, page_cookie_name
from ..routing import current_core, route

bp = Blueprint("network", __name__)

AUDIT_REFUSED = "BuildBot could not write the audit line, so it did not start listening on your home network."


def _net():
    return current_app.extensions[EXTENSION]


@route(bp, "GET", "/v1/network", access=OWNER_DEVICE)
def network_get():
    resp = jsonify(_net().view())
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "PUT", "/v1/network", access=OWNER_DEVICE)
def network_put():
    """Her one step (D130). One audit line per change (no addresses). Without that line it does not open; off always works."""
    from ...zone3.service import for_app
    from ..network import save_on, saved_on
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"on"} or not isinstance(body["on"], bool):
        return jsonify(error='Send {"on": true} or {"on": false}.'), 400
    net, on = _net(), body["on"]
    if on != saved_on(net.state):
        try:
            for_app(current_app).audit.append({"actor": "owner", "action": "network_on" if on else "network_off"})
        except Exception:  # noqa: BLE001 - no audit line: closed stays closed, and off still goes off
            if on:
                return jsonify(error=AUDIT_REFUSED), 503
    save_on(net.state, on)                    # her choice, saved even when it is what the default says
    net.apply()                               # from a device too: it stops listening, and this answer still goes back
    return jsonify(net.view())


@route(bp, "POST", "/v1/network/code", access=OWNER_DEVICE)
def network_code():
    """A pairing code for a device on the network: 12 characters, one use, 10 minutes, as ever (``pairing.py``)."""
    resp = jsonify(code=pairing.new_code(current_core().state), minutes=pairing.LIFETIME_S // 60, urls=_net().view()["urls"])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "GET", "/v1/network/pages", access=OWNER_DEVICE)
def network_pages():
    """A paired browser on the network, both halves shown: its package ports, and a pass for each. The pass is a cookie
    only: HttpOnly, host-only, so no page's script reads it. A cookie reaches every port of a host, so this answer can
    set it for the page ports, and no secret goes in an address. Adopted apps and tries have no port and no pass."""
    if request.environ.get("puffpai.listener") != NETWORK_LISTENER:
        return jsonify(error="This is only for a device on your home network."), 404
    core, net = current_core(), _net()
    ports = net.open_pages()
    resp = jsonify(ports=ports)
    resp.headers["Cache-Control"] = "no-store"
    for package, port in ports.items():
        value = core.tokens.page_pass(g.token, package)
        if value:
            resp.set_cookie(page_cookie_name(port), value, httponly=True, samesite="Strict", path="/",
                            max_age=auth.PAGE_PASS_S)
    return resp


@route(bp, "GET", "/v1/devices", access=OWNER_DEVICE)
def devices_list():
    tokens = current_core().tokens
    mine = tokens.device_id(g.token)
    resp = jsonify(devices=[dict(d, this=d["id"] == mine) for d in tokens.browsers()])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "DELETE", "/v1/devices/<id>", access=OWNER_DEVICE)
def devices_remove(id):
    if not current_core().tokens.revoke_browser(id):
        return jsonify(error="That device is not paired."), 404
    # Now, not at its next request: its shells close, which ends their streams, and every event stream ends. The others
    # come straight back, as they do every 25 seconds, and the gate turns this one away.
    from .dev import shells
    shells().close_device(id)
    current_app.extensions["puffpai.feed"].end_streams()
    return jsonify(removed=id)
