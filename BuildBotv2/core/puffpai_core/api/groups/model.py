"""Core API group `model` (lane L2). The owner's model-slot settings.

- ``GET /v1/model``: ``settings\\model.json`` (name, address, context size,
  tool budget, plus what is in effect) and the brain's named state
  (``not_downloaded``, ``loading``, ``ready``, ``timed_out``, ``error``; D36(d)).
- ``GET /v1/model/menu``: the hardware census and the models this computer
  can run, strongest first, one Recommended, with download sizes (D36(e)).
- ``PUT /v1/model`` ``{model}`` (D133, v1's check in the same order): a name, then its Ollama answers, then that Ollama
  has it. Nothing is saved until all three hold. The next chat turn uses it.
The wizard (lane L5) writes the settings through ``zone2.settings.save``.
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from ...zone2 import models
from ...zone2 import settings as model_settings
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("model", __name__)


@route(bp, "GET", "/v1/model", OWNER)
def model():
    state = current_core().state
    s = model_settings.load(state)
    return jsonify(settings=s.as_dict(), state=models.state(s.model, state))


@route(bp, "PUT", "/v1/model", OWNER_DEVICE)
def model_put():
    state = current_core().state
    body = request.get_json(silent=True)
    name = body.get("model") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return jsonify(error="Say which model: {\"model\": \"<name>\"}."), 400
    name, s = name.strip(), model_settings.load(state)
    if not model_settings._ask_version(s.address):
        return jsonify(error=f"Nothing answers at {s.address}, so nothing was saved. Start Ollama, then try again."), 503
    if not model_settings._has_model(s.address, name):
        return jsonify(error=f"{name} is not on this computer yet, so nothing was saved. Download it first.", code="model_missing"), 400
    s = model_settings.save(state, model=name)
    return jsonify(settings=s.as_dict(), state=models.state(s.model, state), line=f"The brain is now {name}. The next chat turn uses it.")


@route(bp, "GET", "/v1/model/menu", OWNER)
def model_menu():
    state = current_core().state
    # Free disk is measured where the model server keeps its models (Ollama's own setting).
    census = models.census(os.environ.get("OLLAMA_MODELS") or str(Path.home() / ".ollama" / "models"))
    return jsonify(census=census, menu=models.fit(census, state=state))
