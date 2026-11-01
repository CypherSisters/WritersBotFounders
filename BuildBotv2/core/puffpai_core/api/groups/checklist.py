"""Core API group `checklist` (lane L5): "Your turn" steps.

A package token sees, raises and closes its own steps only, and can never
raise an admin step or a command check (D49). An owner caller may name the
package or agent a step is for, but over HTTP no one raises an admin step or a
command check: those come only from the Core in process (approved
``setup_steps``, Toolsmith and the other agents). Majordomo keeps the list and
answers the same intents on the Bus (``checklist.add|list|done|skip``).

Run as administrator needs the owner's PIN in the same request (D29), checked
by the PIN check lane L3 installs (``app.extensions["puffpai.pin_check"]``);
until it exists the route refuses. The command run is the stored step's own
text, the one the card shows, never anything from the request.
"""

from flask import Blueprint, g, jsonify, request

from ...checklist import Checklist, ChecklistError
from ...checklist.store import CheckScope
from ..auth import OWNER_DEVICE, PACKAGE_OK
from ..routing import current_core, route

bp = Blueprint("checklist", __name__)


def init_app(app) -> None:
    core = app.extensions["puffpai"]
    checklist = Checklist(core.state / "checklist.json", bus=core.bus, scope=CheckScope(core.state, core.registry))
    app.extensions["puffpai.checklist"] = checklist
    if core.bus.get("majordomo") is None:
        core.bus.register(checklist.as_agent())


def _checklist() -> Checklist:
    from flask import current_app
    return current_app.extensions["puffpai.checklist"]


def _own() -> str | None:
    """The package id a package token is limited to; None for an owner."""
    return g.caller.package_id if g.caller.kind == "package" else None


def _visible(step_id: str):
    step = _checklist().get(step_id)
    own = _own()
    if step is None or (own is not None and step["package"] != own):
        return None
    return step


@route(bp, "GET", "/v1/checklist", access=PACKAGE_OK)
def checklist_list():
    return jsonify(_checklist().list(package=_own(), include_closed=request.args.get("all") in ("1", "true")))


@route(bp, "POST", "/v1/checklist", access=PACKAGE_OK)
def checklist_add():
    body = request.get_json(silent=True)
    own = _own()
    if own is not None:
        package, trust = own, "package"
    else:
        package = (body or {}).get("package") if isinstance(body, dict) else None
        package, trust = (package if isinstance(package, str) and package else "core"), "owner"
    blocks = (body or {}).get("blocks") if isinstance(body, dict) and own is None else None
    try:
        step = _checklist().add(body, package, blocks, trust=trust)
    except ChecklistError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(step), 201


@route(bp, "GET", "/v1/checklist/<id>", access=PACKAGE_OK)
def checklist_get(id):
    step = _visible(id)
    if step is None:
        return jsonify(error=f"No step with id {id!r}."), 404
    return jsonify(step)


@route(bp, "POST", "/v1/checklist/<id>/done", access=PACKAGE_OK)
def checklist_done(id):
    if _visible(id) is None:
        return jsonify(error=f"No step with id {id!r}."), 404
    ok, step, reason = _checklist().done(id, by=_who())
    if not ok:
        return jsonify(error=f"Not done yet: {reason}", step=step), 409
    return jsonify(step)


@route(bp, "POST", "/v1/checklist/<id>/skip", access=PACKAGE_OK)
def checklist_skip(id):
    if _visible(id) is None:
        return jsonify(error=f"No step with id {id!r}."), 404
    return jsonify(_checklist().skip(id, by=_who()))


PIN_NOT_BUILT = ("Run as administrator needs your PIN, and the PIN check (the vault) is not built yet. "
                 "Copy the command and run it yourself in PowerShell as administrator.")


@route(bp, "POST", "/v1/checklist/<id>/run-admin", access=OWNER_DEVICE)
def checklist_run_admin(id):
    from flask import current_app

    step = _checklist().get(id)
    if step is None:
        return jsonify(error=f"No step with id {id!r}."), 404
    try:
        _admin_ok(step)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    if body.get("factor", "pin") != "pin":
        return jsonify(error="not built yet", lane="L3",
                       reason="Windows Hello is not built yet. Use your PIN."), 501
    pin = body.get("pin")
    if not isinstance(pin, str) or not pin:
        return jsonify(error="Type your PIN to run this as administrator."), 403
    pin_check = current_app.extensions.get("puffpai.pin_check")
    if pin_check is None:
        return jsonify(error="not built yet", lane="L3", reason=PIN_NOT_BUILT), 501
    refused = pin_check(pin)
    if refused:
        status, reason = refused
        return jsonify(error=reason), status
    step = _checklist().get(id)                 # read again: the command is the stored, approved step's
    try:
        run_elevated(step)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True)


def _admin_ok(step: dict) -> None:
    if step.get("state") != "open" or step.get("kind") != "run_command" or not step.get("admin"):
        raise ValueError("Only an open step marked admin can run as administrator.")
    if str(step.get("raised_by", "")).startswith("pkg:"):
        raise ValueError("A package's own step never runs as administrator.")
    if (step.get("shell") or "powershell") != "powershell":
        raise ValueError("Only PowerShell steps run as administrator from here. Copy it and run it yourself.")


def run_elevated(step: dict, popen=None) -> None:
    """Start the step's PowerShell command in an elevated window: Windows shows her the UAC prompt.

    The command goes as -EncodedCommand (base64 UTF-16LE), so no quoting can change it.
    """
    import base64
    import subprocess
    import sys

    _admin_ok(step or {})
    if sys.platform != "win32" and popen is None:
        raise ValueError("Run as administrator is for Windows. Copy the command and run it yourself.")
    encoded = base64.b64encode(step["text"].encode("utf-16-le")).decode("ascii")
    inner = f"Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-EncodedCommand','{encoded}'"
    (popen or subprocess.Popen)(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", inner],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _who() -> str:
    return f"pkg:{g.caller.package_id}" if g.caller.kind == "package" else "owner"


__all__ = ["bp", "init_app", "current_core"]
