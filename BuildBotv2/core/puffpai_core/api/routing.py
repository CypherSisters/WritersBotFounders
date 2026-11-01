"""Route helpers. Every route says who may call it, so the auth middleware can't be skipped.

A lane replaces a ``stub(...)`` line in its group file with a real view:

    @route(bp, "POST", "/v1/chat", access=OWNER)
    def chat():
        caller = g.caller          # auth.Caller, or None on public routes
        core = current_core()      # registry, tokens, bus, state
        ...

Routes and access levels must match ``contracts/openapi.yaml`` (a test checks).
"""

from __future__ import annotations

import re
from typing import Callable

from flask import Blueprint, current_app, jsonify

from .auth import ACCESS_LEVELS


def route(bp: Blueprint, method: str, rule: str, access: str) -> Callable:
    if access not in ACCESS_LEVELS:
        raise ValueError(f"unknown access level {access!r}")

    def deco(fn: Callable) -> Callable:
        fn.puffpai_access = access
        bp.add_url_rule(rule, endpoint=fn.__name__, view_func=fn, methods=[method])
        return fn
    return deco


def stub(bp: Blueprint, method: str, rule: str, access: str, lane: str) -> None:
    """A route that exists but is not built yet: 501 with the lane that fills it in."""
    if access not in ACCESS_LEVELS:
        raise ValueError(f"unknown access level {access!r}")

    def view(**_args):
        return jsonify(error="not built yet", lane=lane), 501

    view.puffpai_access = access
    view.puffpai_lane = lane
    name = "stub_" + method.lower() + re.sub(r"\W+", "_", rule)
    bp.add_url_rule(rule, endpoint=name, view_func=view, methods=[method])


def current_core():
    """The running Core's parts (``api.app.Core``)."""
    return current_app.extensions["puffpai"]
