"""Core API group `brain` (lane L2). Packages ask the local model, never the model server directly.

``POST /v1/brain/chat`` ``{"messages": [{"role", "content"}], "images": [b64, ...]}`` ->
``{"text", "usage": {"prompt_tokens", "completion_tokens"}, "model"}`` (``model``: the local model that
answered, so an app can say "Read by <model> on this computer"; lane I19b). When the brain
cannot answer: 503 ``{"error", "state"}`` (D36(d)). A package gets handles
only: the gate runs with ``vault_open=False``. ``images`` is optional and
allowed only when the picture model (the ``vision`` role, else the chat brain; lane I17) reads pictures.
Each answered ask with pictures is counted for that model (``zone2.roles.note_use``: counts only), so the
Desktop can say what the picture model has read (``GET /v1/models/used``).
"""

from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from ...zone2 import roles
from ...zone2.errors import BrainError
from ..auth import PACKAGE_OK
from ..routing import current_core, route
from .chat import brain, refused

bp = Blueprint("brain", __name__)


@route(bp, "POST", "/v1/brain/chat", PACKAGE_OK)
def brain_chat():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
        return jsonify(error="Send {\"messages\": [{\"role\": \"user\", \"content\": \"...\"}]}."), 400
    images = body.get("images") or []
    if not isinstance(images, list) or not all(isinstance(i, str) for i in images):
        return jsonify(error="images is a list of base64 strings."), 400
    b = brain()
    if images and not b.can_read_images():
        return jsonify(error="This brain cannot read pictures. Add a picture model on the Developer page."), 400
    caller = getattr(g, "caller", None)
    pid = str(getattr(caller, "package_id", "") or "") or None
    try:
        # WritersBot: as written only when the app declares it (the owner saw it on the Yes card) AND this call asks
        # for it, so the app's own switch ("Keep manuscripts out of the vault", on by default) decides each time.
        got = b.package_chat(messages, images, as_written=bool(body.get("as_written") is True and b.package_as_written(pid)))
    except BrainError as exc:
        return refused(exc)
    if images:
        caller = getattr(g, "caller", None)
        roles.note_use(current_core().state, "vision", str(got.get("model") or ""),
                       by=str(getattr(caller, "package_id", "") or ""), n=len(images))
    return jsonify(got)
