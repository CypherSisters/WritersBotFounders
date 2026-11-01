"""Core API group ``toolsmith`` (lane L6): Toolsmith's model, plans, builds and jobs; and Toolsmith's own routes.

For the owner (access ``owner`` / ``owner_device``): the wizard (lane L5)
calls these for step 5 (Toolsmith's model), steps 6 to 10 (goal -> plan ->
ONE Yes -> build -> prove), and the Build page. The Yes itself is the owner's
answer on a Core consent card (``/v1/consent/{id}/answer``); ``start`` only
checks that a Yes for exactly this plan exists.

For Toolsmith's own process (access ``toolsmith``, under
``/v1/toolsmith/agent``): its token is bound to one build, so no route takes
a build id. Each route does one thing the Core does for it (09 section 3):
run a command (the broker), write a file (zone checked), one model turn
("lend and send"), the Drawbridge check, add to the Package Manager (bound to
the checked tree hash), Checklist steps, and job status writes. A route that
can't do what was asked answers 409 with ``{error, kind, code}``.
"""

from __future__ import annotations

import functools

from flask import Blueprint, current_app, g, jsonify, request

from ...zone3.toolsmith import errors
from ...zone3.toolsmith_host import ToolsmithHost
from ...zone3.toolsmith_host.service import Paused
from ...zone3.toolsmith_host import redact, sender
from ...zone3.toolsmith_host.ports import Ports
from ..auth import OWNER, OWNER_DEVICE, TOOLSMITH
from ..routing import current_core, route

bp = Blueprint("toolsmith", __name__)
A = "/v1/toolsmith/agent"


def service() -> ToolsmithHost:
    """One Toolsmith host per running Core (made on first use)."""
    ext = current_app.extensions
    if "puffpai_toolsmith" not in ext:
        core = current_core()
        app = current_app._get_current_object()
        ports = Ports(core.state, registry=core.registry, checklist=app.extensions.get("puffpai.checklist"),
                      consent=getattr(core, "consent", None), bus=core.bus,
                      zone=lambda: app.extensions.get("puffpai.zone3"),   # this Core's own Zone 3
                      packages=getattr(core, "packages", None), steward=getattr(core, "steward", None),
                      caretaker=getattr(core, "caretaker", None))
        api = current_app.config.get("PUFFPAI_API_URL") or "http://127.0.0.1:8420"

        def picker(package: str, ask: str, store: list | None = None) -> dict:
            # Reuse is checked by the same Brain chat uses: its package tools come with their tokens.
            with app.app_context():
                from .chat import brain
                return brain().picks(package, ask, store=store)
        ports.picker = picker
        ext["puffpai_toolsmith"] = ToolsmithHost(core.state, ports, core.registry, core.tokens, api=api)
    return ext["puffpai_toolsmith"]


def _body() -> dict:
    got = request.get_json(silent=True)
    return got if isinstance(got, dict) else {}


# -- the owner's routes ------------------------------------------------------------

@route(bp, "GET", "/v1/toolsmith", access=OWNER)
def toolsmith_status():
    return jsonify(service().status())


@route(bp, "GET", "/v1/toolsmith/providers", access=OWNER)
def toolsmith_providers():
    return jsonify([sender.describe(p) for p in service().providers().values()])


@route(bp, "PUT", "/v1/toolsmith/model", access=OWNER_DEVICE)
def toolsmith_model():
    try:
        got = service().choose(_body(), by=getattr(g.caller, "label", "") or "owner")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify({k: got.get(k) for k in ("where", "provider", "model", "cost_cap_usd")})


@route(bp, "POST", "/v1/toolsmith/plan", access=OWNER)
def toolsmith_plan():
    body = _body()
    goal = str(body.get("goal") or "").strip()
    if not goal and not body.get("jobs"):
        return jsonify(error="Say what the bot should do (a goal), or give its jobs."), 400
    try:
        return jsonify(service().plan(goal, body.get("jobs"))), 201
    except Paused as exc:                          # safe mode: Toolsmith is paused until restart
        return jsonify(dict(errors.to_wire(exc), safe_mode=True)), 409


@route(bp, "GET", "/v1/toolsmith/builds/<build_id>", access=OWNER)
def toolsmith_build(build_id):
    try:
        return jsonify(service().view(build_id))
    except KeyError:
        return jsonify(error=f"There is no build {build_id}."), 404


@route(bp, "POST", "/v1/toolsmith/builds/<build_id>/start", access=OWNER)
def toolsmith_start(build_id):
    try:
        return jsonify(service().start(build_id, str(_body().get("consent_id") or "")))
    except KeyError:
        return jsonify(error=f"There is no build {build_id}."), 404
    except PermissionError as exc:
        return jsonify(error=str(exc)), 409
    except Paused as exc:
        return jsonify(dict(errors.to_wire(exc), safe_mode=True)), 409
    except errors.Refused as exc:                  # a "Check again" card's Yes that can't start now (lane I18e)
        return jsonify(error=str(exc)), 409


@route(bp, "POST", "/v1/toolsmith/builds/<build_id>/again", access=OWNER)
def toolsmith_again(build_id):
    """"Check again" (lane I18e): the build's unfinished jobs are tried once more, under her Yes when the consent
    rules still allow it, else after ONE new Yes card (``{consent_id}``: her Yes on that card)."""
    try:
        return jsonify(service().again(build_id, str(_body().get("consent_id") or "")))
    except KeyError:
        return jsonify(error=f"There is no build {build_id}."), 404
    except Paused as exc:
        return jsonify(dict(errors.to_wire(exc), safe_mode=True)), 409
    except (PermissionError, errors.Refused) as exc:
        return jsonify(error=str(exc)), 409


@route(bp, "PUT", "/v1/toolsmith/builds/<build_id>/use", access=OWNER_DEVICE)
def toolsmith_use(build_id):
    """Her ticks on the plan card's offers (``{consent_id, use: [job numbers]}``), before she says Yes."""
    body = _body()
    try:
        got = service().use_offers(build_id, str(body.get("consent_id") or ""), body.get("use"),
                                   by=getattr(g.caller, "label", "") or "owner")
    except KeyError:
        return jsonify(error=f"There is no build {build_id}."), 404
    except PermissionError as exc:
        return jsonify(error=str(exc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, use=got)


@route(bp, "PUT", "/v1/toolsmith/builds/<build_id>/holds/<consent_id>", access=OWNER_DEVICE)
def toolsmith_hold_ticks(build_id, consent_id):
    """Her ticks on a job's hold card (``{try: [candidate refs]}``): which set-aside apps Toolsmith may try."""
    try:
        got = service().trials.tick_holds(build_id, consent_id, _body().get("try"),
                                          by=getattr(g.caller, "label", "") or "owner")
    except PermissionError as exc:
        return jsonify(error=str(exc)), 409
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(ok=True, ticked=got)


@route(bp, "GET", "/v1/toolsmith/jobs/<job_id>", access=OWNER)
def toolsmith_job(job_id):
    try:
        return jsonify(service().job(job_id))
    except KeyError:
        return jsonify(error=f"There is no job {job_id}."), 404


@route(bp, "POST", "/v1/toolsmith/jobs/<job_id>/next", access=OWNER)
def toolsmith_next(job_id):
    try:
        service().job(job_id)
    except KeyError:
        return jsonify(error=f"There is no job {job_id}."), 404
    service().next_app(job_id)
    return jsonify(ok=True, said="Toolsmith ends this try and moves to the next app.")


# -- Toolsmith's own routes (its token, its one build) --------------------------------

def agent(fn):
    """Call the host for the caller's own build; every failure is a plain answer, never a crash."""
    @functools.wraps(fn)
    def view(**kw):
        build_id = getattr(g.caller, "build_id", None)
        if not build_id:
            return jsonify(error="This route is for Toolsmith's own process only."), 403
        try:
            out = fn(service(), build_id, **kw)
        except errors.ALL as exc:
            return jsonify(redact.scrub(errors.to_wire(exc))), 409
        except KeyError:
            return jsonify(error="There is no such thing in this build."), 404
        except PermissionError as exc:
            return jsonify(error=redact.scrub_text(str(exc))), 403
        except ValueError as exc:
            return jsonify(error=redact.scrub_text(str(exc))), 400
        except Exception as exc:  # noqa: BLE001 - a plain line for Toolsmith, which holds the job
            return jsonify(error=f"The Core could not do that for Toolsmith ({type(exc).__name__})."), 500
        return jsonify(out)
    return view


@route(bp, "GET", A + "/build", access=TOOLSMITH)
@agent
def agent_build(host, build_id):
    return host.agent_build(build_id)


@route(bp, "PUT", A + "/plan", access=TOOLSMITH)
@agent
def agent_plan(host, build_id):
    return host.agent_plan(build_id, _body())


@route(bp, "POST", A + "/fits", access=TOOLSMITH)
@agent
def agent_fits(host, build_id):
    return host.agent_fits(build_id, _body())


@route(bp, "PUT", A + "/now", access=TOOLSMITH)
@agent
def agent_now(host, build_id):
    return host.agent_now(build_id, _body())


@route(bp, "POST", A + "/jobs/<job_id>/status", access=TOOLSMITH)
@agent
def agent_job_status(host, build_id, job_id):
    return host.agent_job_status(build_id, job_id, _body())


@route(bp, "POST", A + "/jobs/<job_id>/holds", access=TOOLSMITH)
@agent
def agent_holds_ask(host, build_id, job_id):
    return host.agent_holds_ask(build_id, job_id)


@route(bp, "GET", A + "/jobs/<job_id>/holds", access=TOOLSMITH)
@agent
def agent_holds_state(host, build_id, job_id):
    return host.agent_holds_state(build_id, job_id)


@route(bp, "POST", A + "/jobs/<job_id>/found", access=TOOLSMITH)
@agent
def agent_found(host, build_id, job_id):
    return host.agent_found(build_id, job_id, _body())


@route(bp, "GET", A + "/registry", access=TOOLSMITH)
@agent
def agent_registry(host, build_id):
    return host.agent_registry(build_id)


@route(bp, "POST", A + "/web", access=TOOLSMITH)
@agent
def agent_web(host, build_id):
    return host.agent_web(build_id, _body())


@route(bp, "POST", A + "/read", access=TOOLSMITH)
@agent
def agent_read(host, build_id):
    return host.agent_read(build_id, _body())


@route(bp, "POST", A + "/queries", access=TOOLSMITH)
@agent
def agent_queries(host, build_id):
    return host.agent_queries(build_id, _body())


@route(bp, "POST", A + "/knowledge", access=TOOLSMITH)
@agent
def agent_knowledge(host, build_id):
    return host.agent_knowledge(build_id)


@route(bp, "POST", A + "/knowledge/find", access=TOOLSMITH)
@agent
def agent_knowledge_find(host, build_id):
    return host.agent_knowledge_find(build_id, _body())


@route(bp, "POST", A + "/jobs/<job_id>/library", access=TOOLSMITH)
@agent
def agent_library(host, build_id, job_id):
    return host.agent_library(build_id, job_id)


@route(bp, "POST", A + "/model", access=TOOLSMITH)
@agent
def agent_model(host, build_id):
    return host.agent_model(build_id, _body())


@route(bp, "POST", A + "/brain/json", access=TOOLSMITH)
@agent
def agent_brain_json(host, build_id):
    return host.agent_brain_json(build_id, _body())


@route(bp, "POST", A + "/brain/answer", access=TOOLSMITH)
@agent
def agent_brain_answer(host, build_id):
    return host.agent_answer(build_id, _body())


@route(bp, "POST", A + "/page", access=TOOLSMITH)
@agent
def agent_page(host, build_id):
    return host.agent_page(build_id, _body())


@route(bp, "GET", A + "/apps/<pkg_id>/read", access=TOOLSMITH)
@agent
def agent_read_app(host, build_id, pkg_id):
    return host.agent_read_app(build_id, pkg_id)


@route(bp, "POST", A + "/tries", access=TOOLSMITH)
@agent
def agent_try_open(host, build_id):
    return host.agent_try_open(build_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/download", access=TOOLSMITH)
@agent
def agent_try_download(host, build_id, try_id):
    return host.agent_try_download(build_id, try_id)


@route(bp, "POST", A + "/tries/<try_id>/check", access=TOOLSMITH)
@agent
def agent_try_check(host, build_id, try_id):
    return host.agent_try_check(build_id, try_id)


@route(bp, "POST", A + "/tries/<try_id>/run", access=TOOLSMITH)
@agent
def agent_try_run(host, build_id, try_id):
    return host.agent_try_run(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/start", access=TOOLSMITH)
@agent
def agent_try_start(host, build_id, try_id):
    return host.agent_try_start(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/stop", access=TOOLSMITH)
@agent
def agent_try_stop(host, build_id, try_id):
    return host.agent_try_stop(build_id, try_id, _body())


@route(bp, "GET", A + "/tries/<try_id>/log", access=TOOLSMITH)
@agent
def agent_try_log(host, build_id, try_id):
    return host.agent_try_log(build_id, try_id)


@route(bp, "POST", A + "/tries/<try_id>/files", access=TOOLSMITH)
@agent
def agent_try_write(host, build_id, try_id):
    return host.agent_try_write(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/mcp", access=TOOLSMITH)
@agent
def agent_try_mcp(host, build_id, try_id):
    return host.agent_try_mcp(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/fs", access=TOOLSMITH)
@agent
def agent_try_fs(host, build_id, try_id):
    return host.agent_try_fs(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/end", access=TOOLSMITH)
@agent
def agent_try_end(host, build_id, try_id):
    return host.agent_try_end(build_id, try_id, _body())


@route(bp, "POST", A + "/tries/<try_id>/add", access=TOOLSMITH)
@agent
def agent_try_add(host, build_id, try_id):
    return host.agent_try_add(build_id, try_id, _body())


@route(bp, "GET", A + "/tries/<try_id>/add", access=TOOLSMITH)
@agent
def agent_try_add_state(host, build_id, try_id):
    return host.agent_try_add_state(build_id, try_id)


@route(bp, "POST", A + "/steps", access=TOOLSMITH)
@agent
def agent_step_add(host, build_id):
    return host.agent_step_add(build_id, _body())


@route(bp, "GET", A + "/steps/<step_id>", access=TOOLSMITH)
@agent
def agent_step_state(host, build_id, step_id):
    return host.agent_step_state(build_id, step_id)
