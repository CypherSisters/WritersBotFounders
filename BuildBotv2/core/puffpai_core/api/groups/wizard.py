"""Core API group `wizard` (lane L5): steps, progress and answers, for the browser renderer.

``GET /v1/wizard`` is the step list with each step's state. ``POST
/v1/wizard/<step>`` with ``{"answers": {...}, "renderer": "browser"}`` returns
the next screen (``wizard.engine.Engine.submit``). The terminal renderer
(``puffpai setup``) calls the same engine in process. A reply may carry the
recovery code, so every reply here is ``no-store``, like the vault routes (D106).
"""

from flask import Blueprint, current_app, request

from ...wizard import Engine, StepListError, default_services
from ..auth import OWNER
from ..routing import route
from .vault import _reply

bp = Blueprint("wizard", __name__)


def init_app(app) -> None:
    app.extensions["puffpai.wizard"] = None          # made on first use, so other lanes' parts are in place


def brain_json(app):
    """The local brain's one small JSON ask, thinking off (the name and look steps): the same Brain chat uses."""
    def ask(system: str, user: str):
        with app.app_context():
            from .chat import brain
            return brain().ask_json(system, user, think=False)
    return ask


def engine() -> Engine:
    eng = current_app.extensions.get("puffpai.wizard")
    if eng is None:
        core = current_app.extensions["puffpai"]
        services = default_services(core)
        services.brain = brain_json(current_app._get_current_object())
        eng = Engine(core.state, services, registry=core.registry, bus=core.bus)
        current_app.extensions["puffpai.wizard"] = eng
    return eng


@route(bp, "GET", "/v1/wizard", access=OWNER)
def wizard_overview():
    try:
        return _reply(engine().overview())
    except StepListError as exc:
        return _reply({"error": str(exc)}, 500)


@route(bp, "POST", "/v1/wizard/<step>", access=OWNER)
def wizard_answer(step):
    body = request.get_json(silent=True) or {}
    answers = body.get("answers") if isinstance(body, dict) else None
    if answers is not None and not isinstance(answers, dict):
        return _reply({"error": "answers is one object: {\"<field id>\": value}."}, 400)
    try:
        eng = engine()
        return _reply(eng.submit(step, answers or {}, renderer=body.get("renderer", "browser")))
    except KeyError:
        return _reply({"error": f"There is no setup step {step!r}."}, 404)
    except StepListError as exc:
        return _reply({"error": str(exc)}, 500)
