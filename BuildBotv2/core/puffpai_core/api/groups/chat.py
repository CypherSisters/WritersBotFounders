"""Core API group `chat` (lane L2). One conversation through Concierge (chat, voice and routines share it).

- ``POST /v1/chat`` ``{"text", "conversation"?}`` -> ``{"text", "empty", "notice", "changed", "used", "steps"}``.
  ``text`` is the brain's answer exactly as it said it; ``changed`` lists the
  ``changes`` tool calls that really ran. An empty answer is ``text: ""`` with
  ``notice: "The brain gave no answer."``.
- ``POST /v1/chat/stream``: the same turn as NDJSON lines: ``progress``,
  ``token``, ``ask``, ``answered``, ``open``, ``tool_done``, then ``done``
  (the fields above) or ``error``.
- ``POST /v1/chat/tool`` ``{"tool": "<pkg>.<name>", "args": {}}``: one tool,
  with the same Yes as in chat.
- ``GET /v1/chat/history`` ``?conversation=&limit=``: one conversation's words in the locked form, for a paired-device
  token only (her paired browsers, and this computer's terminal key, which is one by design: the chat needs it).

Every package tool call goes through Drawbridge's review (``wiring.tool_review``,
set by ``start_core``). A call outside her approved scope raises a ``tool_call``
Yes card and the request waits for her answer (up to the card's 10 minutes);
her No or an expired card ends the turn with one plain line.

A turn the brain cannot run answers 503 ``{"error", "state"}`` with the real
reason (D36(d)); with no privacy gate nothing runs at all (fail closed).
"""

from __future__ import annotations

import json
import queue
import threading

from flask import Blueprint, Response, g, jsonify, request

from ... import wiring
from ...zone2 import conversations
from ...zone2.brain import Brain, register
from ...zone2.errors import BrainError
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("chat", __name__)


def brain() -> Brain:
    """The running Core's Brain, built once (tests put their own in ``app.extensions``)."""
    from flask import current_app
    ext = current_app.extensions
    if "puffpai_brain" not in ext:
        core = current_core()
        steward = getattr(core, "steward", None)          # lane L1: Steward(...).token(id), .start(id)
        ext["puffpai_brain"] = Brain(core.registry, core.state,
                                     token=steward.token if steward is not None else None,
                                     start=_starter(steward) if steward is not None else None,
                                     # The Core's one reader of an installed package's manifest: the file
                                     # her install Yes covered, by its sha256 (never a file read here).
                                     manifest=getattr(steward, "read_manifest", None),
                                     folder=getattr(getattr(steward, "installs", None), "folder", None),
                                     review=ext.get("puffpai.tool_review"),   # Drawbridge, every call (wiring)
                                     consent=ext.get("puffpai.core_question"),  # read_app's start card (wiring)
                                     checklist=ext.get("puffpai.checklist"),
                                     search=wiring.knowledge_search(core),
                                     # read_pinned (D125), send_document (D127)
                                     pinned=wiring.PinnedDocuments(core, ext.get("puffpai.tool_review")),
                                     toolsmith=ext.get("puffpai.ask_toolsmith"),   # chat asks Toolsmith (wiring)
                                     outside=_tool_servers(core, current_app._get_current_object()),   # D137
                                     # The reading Drawbridge's scope uses, from the consent store at each call (D110).
                                     reaches=getattr(getattr(core, "packages", None), "_has_address", None),
                                     audit=_audit_line(current_app._get_current_object()),   # her programs (D16)
                                     safe_mode=lambda: bool(core.caretaker.in_safe_mode))
        register(ext["puffpai_brain"])
    return ext["puffpai_brain"]


def _tool_servers(core, app):
    """The tool servers she added (D137), with the app's Zone 3 asked only when one is off this computer."""
    from ...tool_servers import ToolServers
    from ...zone3.service import for_app
    return ToolServers(core, lambda: for_app(app))


#: How often a quiet chat stream says something, to notice that the chat closed.
KEEPALIVE_S = 5.0


def _audit_line(app):
    def line(record: dict) -> None:
        from ...zone3.service import for_app
        for_app(app).audit.append(record)
    return line


def _starter(steward):
    """Steward's ``start(id) -> row`` as ``read_app`` wants it: ``{ok, port, said}``."""
    def start(pid: str) -> dict:
        try:
            row = steward.start(pid) or {}
        except Exception as exc:  # noqa: BLE001 - StewardError carries a plain message
            return {"ok": False, "said": str(exc)}
        return {"ok": row.get("status") == "running" or bool(row.get("port")), "port": row.get("port")}
    return start


def refused(exc: BrainError):
    return jsonify(error=exc.message, state=exc.code), 503


def _her_device() -> bool:
    """Her "You" notes go into the chat only for her paired owner device, never an owner API key (lane I12)."""
    caller = getattr(g, "caller", None)
    return caller is not None and caller.kind == "owner" and caller.via == "device"


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@route(bp, "POST", "/v1/chat", OWNER)
def chat():
    body = _body()
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify(error="Send {\"text\": \"...\"}: what to ask the bot."), 400
    try:
        return jsonify(brain().turn(text, conversation=str(body.get("conversation") or "main"),
                                    personal=_her_device()))
    except BrainError as exc:
        return refused(exc)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@route(bp, "POST", "/v1/chat/stream", OWNER)
def chat_stream():
    body = _body()
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify(error="Send {\"text\": \"...\"}: what to ask the bot."), 400
    b = brain()
    conversation = str(body.get("conversation") or "main")
    events: "queue.Queue[dict | None]" = queue.Queue()
    cancelled = threading.Event()
    personal = _her_device()

    def work() -> None:
        try:
            b.turn(text, conversation=conversation, emit=events.put, cancelled=cancelled, personal=personal)
        except BrainError as exc:
            events.put({"type": "error", "error": exc.message, "state": exc.code})
        except Exception as exc:  # noqa: BLE001 - the page gets a plain line
            events.put({"type": "error", "error": f"Something went wrong: {type(exc).__name__}", "state": "error"})
        finally:
            events.put(None)
    threading.Thread(target=work, daemon=True, name="chat-turn").start()

    def lines():
        try:
            while True:
                try:
                    event = events.get(timeout=KEEPALIVE_S)
                except queue.Empty:
                    yield "\n"     # a blank line every client skips: how a closed chat is noticed, so its command stops
                    continue
                if event is None:
                    return
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            cancelled.set()
    return Response(lines(), mimetype="application/x-ndjson")


@route(bp, "GET", "/v1/chat/history", OWNER_DEVICE)
def chat_history():
    """One conversation's words for a paired-device token (the PuffPAI window, D81; the terminal key is one too),
    newest last, in the store's
    locked form (X6: handles, never held values), so a page need not keep her words in a plain store of its own."""
    try:
        limit = max(1, min(int(request.args.get("limit", 200)), 500))
    except ValueError:
        return jsonify(error="limit is a number of lines."), 400
    store = conversations.ConversationStore(current_core().state / "chat")
    try:
        lines = store.load(str(request.args.get("conversation") or conversations.DEFAULT), limit=limit)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    resp = jsonify([{"role": m.get("role"), "content": m.get("content"), "at": m.get("at"), "changed": m.get("changed") or []}
                    for m in lines if isinstance(m, dict) and m.get("role") in ("user", "assistant")])
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "POST", "/v1/chat/tool", OWNER)
def chat_tool():
    body = _body()
    tool = str(body.get("tool") or "")
    args = body.get("args", {})
    if not tool or args is not None and not isinstance(args, dict):
        return jsonify(error="Send {\"tool\": \"<package>.<tool>\", \"args\": {}}."), 400
    args = args or {}
    try:
        got = brain().call_tool(tool, args)
    except BrainError as exc:
        return refused(exc)
    except Exception as exc:  # noqa: BLE001 - McpError and friends carry a plain message
        return jsonify(ok=False, error=getattr(exc, "message", str(exc))), 502
    if got.get("bad_args"):
        return jsonify(got), 400                # unknown or wrong arguments: refused, with the plain reason
    return jsonify(got), (200 if got.get("ok") else 422)
