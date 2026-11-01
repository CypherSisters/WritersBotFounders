"""Core API group `models` (lane I17). Which local model does which job, and adding one.

- ``GET /v1/models``: the models on this computer's Ollama and what each is used for, every catalog row with
  its roles, whether it ``fits`` this computer and its download size, the best fitting rows per role, the
  role map, and any download asked for from here.
- ``PUT /v1/models/roles`` ``{role: model, ...}``: set roles (chat, code, vision, fast) to installed models.
  "" takes a role back to chat's model; chat always has one. Chat's model is ``settings\\model.json``'s, so
  ``GET /v1/model`` reads the same brain.
- ``POST /v1/models/pull`` ``{model}``: a catalog row that fits this computer, after its own Yes card (the
  model, its size and the host Ollama downloads from). Ollama downloads it itself, around the Bridge
  (``zone2.roles``). Progress is in ``GET /v1/models`` (``pulls``), and the end is a notice. A second ask
  for the same model while its card waits gets that same card.
- ``GET /v1/models/used`` (lane I19b): per role, the model doing it now, whether it is the role's own, and
  what it has done (``count``, ``last_at``, ``last_by``): for vision, the pictures apps sent it. Counts only,
  read from ``settings\\model_use.json``; no model server is asked.
- ``POST /v1/models/pull/cancel`` ``{model}``: stop it (its card closes, or its download stops); no role is
  set.

Roles take only local models (``zone2.roles.local_problem``): never a hosted one.

Her paired devices only (``owner_device``).
"""

from __future__ import annotations

import threading

from flask import Blueprint, current_app, jsonify, request

from ...zone2 import models, roles
from ...zone2.ollama import NO_PULL_LINE, ONLY_LINE, only_model
from ..auth import OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("models", __name__)
_PULL_LOCK = threading.Lock()                  # one check-and-record per pull ask at a time

BRIDGE_LINE = ("Ollama downloads models itself, from registry.ollama.ai, so a download goes around the Bridge. "
               "Each one asks your Yes first.")


def _installed(state) -> tuple[list[dict], str]:
    try:
        return list(models.runtime(state).models()), ""
    except (OSError, ValueError):
        return [], "The model server is not running, so the models on this computer can't be listed."


@route(bp, "GET", "/v1/models", OWNER_DEVICE)
def models_list():
    state = current_core().state
    here, problem = _installed(state)
    names = [str(m.get("name") or "") for m in here]
    role_map = roles.role_map(state)
    census = roles.census(state)
    rows = roles.rows(census, names)
    from ...zone2.catalog import is_installed
    installed = [{"name": n, "size_gb": round((m.get("size_bytes") or 0) / 1e9, 1) if m.get("size_bytes") else None,
                  "params": m.get("params"),
                  "used_for": [r for r, model in role_map.items() if model and is_installed(model, [n])]}
                 for n, m in zip(names, here)]
    progress = roles.pulls(state)
    for model, rec in progress.items():
        if rec.get("state") == "downloading":
            got = models.state(model, state)
            if got.get("size_gb"):
                rec.update(done_gb=got.get("done_gb"), size_gb=got.get("size_gb"))
    return jsonify(installed=installed, roles=role_map, catalog=rows,
                   recommended={r: [x["id"] for x in roles.for_role(r, rows)] for r in roles.ROLES},
                   words=roles.ROLE_WORDS, pulls=progress, problem=problem, pull_host=roles.PULL_HOST,
                   bridge=BRIDGE_LINE, total_ram_gb=(census.get("memory") or {}).get("total_gb"),
                   free_disk_gb=(census.get("disk") or {}).get("free_gb"))


@route(bp, "GET", "/v1/models/used", OWNER_DEVICE)
def models_used():
    return jsonify(roles.used(current_core().state))


@route(bp, "PUT", "/v1/models/roles", OWNER_DEVICE)
def models_roles():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify(error='Send {"<role>": "<installed model>"}, for example {"code": "qwen2.5-coder:7b"}.'), 400
    if any(r not in roles.ROLES for r in body) or not all(isinstance(v, str) for v in body.values()):
        return jsonify(error="A role is chat, code, vision or fast, and its model is a name."), 400
    state = current_core().state
    here, problem = _installed(state)
    names = [str(m.get("name") or "") for m in here]
    for role, model in body.items():                  # every change is checked before any is kept
        if only_model() and model.strip() and model.strip() != only_model():
            return jsonify(error=ONLY_LINE.format(only=only_model(), model=model.strip())), 409
        if role == "chat" and not model.strip():
            return jsonify(error="Chat always has a model. Pick another installed model for it instead."), 400
        said = roles.name_problem(model) if model.strip() else ""
        if said:
            return jsonify(error=said), 400
        if model.strip() and not roles.catalog.is_installed(model.strip(), names):
            return jsonify(error=problem or f"{model} is not on this computer yet. Add it first."), 400
        said = roles.local_problem(state, model.strip()) if model.strip() else ""
        if said:
            return jsonify(error=said), 400
    got = {}
    for role, model in body.items():
        try:
            got = roles.set_role(state, role, model, names)
        except roles.RoleError as exc:
            return jsonify(error=str(exc)), 400
    return jsonify(roles=got)


@route(bp, "POST", "/v1/models/pull", OWNER_DEVICE)
def models_pull():
    body = request.get_json(silent=True)
    model = str((body or {}).get("model") or "").strip() if isinstance(body, dict) else ""
    if not model:
        return jsonify(error='Send {"model": "<a model from the list>"}.'), 400
    if only_model():
        return jsonify(error=NO_PULL_LINE.format(only=only_model(), model=model)), 409
    core = current_core()
    state = core.state
    here, problem = _installed(state)
    if problem:
        return jsonify(error=problem), 503
    rows = roles.rows(roles.census(state), [str(m.get("name") or "") for m in here])
    row = next((r for r in rows if r["id"] == model), None)
    if row is None:
        return jsonify(error="Only a model from the list can be added here."), 400
    if row["installed"]:
        return jsonify(error=f"{row['label']} is on this computer already."), 409
    if not row["fits"]:
        return jsonify(error=f"{row['label']} does not fit this computer."), 400
    from ...consent import card_view
    from ...zone3.service import for_app
    consent = for_app(current_app).consents
    with _PULL_LOCK:                           # the check and the record are one step: never two cards
        now = roles.pulls(state).get(model) or {}
        if now.get("state") == "asking" and now.get("consent_id"):
            rec = consent.get(now["consent_id"]) or {}
            if rec.get("status") == "pending":   # the card that waits already
                seen = card_view(rec.get("card") or {}, False)
                return jsonify(consent_id=now["consent_id"], card_sha=rec.get("card_sha"), title=seen["title"],
                               lines=seen["lines"], again=True), 202
        if now.get("state") == "downloading":
            return jsonify(error=f"{row['label']} is being downloaded already."), 409
        scope, card = roles.pull_card(row)
        cid = consent.request(scope, card, False)
        roles.note_pull(state, model, state="asking", consent_id=cid, label=row["label"],
                        line=f"{row['label']}: its Yes card is waiting on Your turn.")
    notices = getattr(core, "notices", None)

    def notify(title: str, text: str) -> None:
        if notices is not None:
            try:
                notices.post("models", {"title": title[:120], "text": text[:600]})
            except Exception:  # noqa: BLE001 - the Developer page shows the end anyway
                pass

    threading.Thread(target=roles.pull_after_yes, args=(state, row, consent, cid), kwargs={"notify": notify},
                     name=f"model-pull-{model}", daemon=True).start()
    rec = consent.get(cid) or {}
    seen = card_view(rec.get("card") or {}, False)
    return jsonify(consent_id=cid, card_sha=rec.get("card_sha"), title=seen["title"], lines=seen["lines"]), 202


@route(bp, "POST", "/v1/models/pull/cancel", OWNER_DEVICE)
def models_pull_cancel():
    body = request.get_json(silent=True)
    model = str((body or {}).get("model") or "").strip() if isinstance(body, dict) else ""
    if not model:
        return jsonify(error='Send {"model": "<the model being added>"}.'), 400
    from ...zone3.service import for_app
    state = current_core().state
    if not roles.cancel_pull(state, model, for_app(current_app).consents):
        return jsonify(error=f"{model} is not being added."), 404
    return jsonify(cancelled=model, pull=roles.pulls(state).get(model))
