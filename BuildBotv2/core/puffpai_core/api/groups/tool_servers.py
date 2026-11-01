"""Core API group ``tool_servers`` (lane api-tools, D137): the owner adds an outside MCP tool server by its web address.

The rules, the record and her Yes: ``puffpai_core/tool_servers.py``. These routes are its only writers from outside the
Core. Nothing here grants: a server off this computer waits for her Yes on the Bridge's address card (D110).

- ``GET /v1/tools/servers``: every row, never a password (only ``has_password``).
- ``POST /v1/tools/servers`` ``{name, address, password?}``: on this computer it connects at once and keeps it only if it
  answers (200); anywhere else it is saved "waiting" and the reply carries the address card for the Desktop's inline Yes
  (202). A password in the query string is refused (400), and nothing is kept.
- ``DELETE /v1/tools/servers/<id>``: the row, its password in the vault, and its address Yes and grant.
- ``POST /v1/tools/servers/<id>/refresh``: asks it for its tools again (after her Yes, for one off this computer).
"""

from __future__ import annotations

from flask import Blueprint, current_app, request

from ... import tool_servers as servers
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route
from .zones import _body, _no_store

bp = Blueprint("tool_servers", __name__)
LANE = "api-tools"


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def service() -> servers.ToolServers:
    from ...zone3.service import for_app
    app = current_app._get_current_object()
    return servers.ToolServers(current_core(), lambda: for_app(app))


def _secret_in_address():
    if any(name in request.args for name in servers.SECRET_ARGS):
        return _no_store({"error": servers.QUERY_LINE, "line": servers.QUERY_LINE}, 400)
    return None


@built("GET", "/v1/tools/servers", OWNER)
def tool_servers_get():
    s = service()
    return _no_store({"servers": s.listed(), "vault": s._vault_state() or "not_set_up"})


@built("POST", "/v1/tools/servers", OWNER_DEVICE)
def tool_servers_post():
    refused = _secret_in_address()
    if refused is not None:
        return refused
    body = _body()
    status, out = service().add(body.get("name"), body.get("address"), body.get("password"), by="owner")
    body = {}                                                  # noqa: F841 - the password is not kept here
    return _no_store(out, status)


@built("DELETE", "/v1/tools/servers/<server_id>", OWNER_DEVICE)
def tool_server_delete(server_id: str):
    status, out = service().remove(server_id)
    return _no_store(out, status)


@built("POST", "/v1/tools/servers/<server_id>/refresh", OWNER_DEVICE)
def tool_server_refresh(server_id: str):
    refused = _secret_in_address()
    if refused is not None:
        return refused
    status, out = service().refresh(server_id)
    return _no_store(out, status)
