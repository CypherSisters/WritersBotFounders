"""Core API group ``openai_api`` (lane api-tools, D137): OpenAI-shaped and Ollama-shaped chat, for Home Assistant's
conversation agent and any OpenAI-style program. A port of v1's routes (``api_server.py`` ``/v1/chat/completions``,
``/api/chat``, ``/v1/models``, ``/api/tags``), under a prefix because ``GET /v1/models`` already means the Desktop's list.

- ``GET /openai/v1/models``, ``GET /ollama/api/tags``: one model, ``puffpai``.
- ``POST /openai/v1/chat/completions`` ``{messages, stream?, conversation_id? | user?}``: the last user message is the
  question; system messages are dropped; earlier turns are not replayed, the bot keeps each conversation itself.
  ``stream: true`` sends ``text/event-stream`` chunks, a final chunk with ``puffpai_meta``, then ``data: [DONE]``.
- ``POST /ollama/api/chat``: the same turn in Ollama's shape; ``stream: true`` sends NDJSON lines.

One turn through the same Brain as ``/v1/chat`` (``chat.brain()``), never a second pipeline, and never her "You" notes
(``personal`` is always False: see ``chat._her_device``). The answer is the brain's text as is. The message text is
never logged.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid

from flask import Blueprint, Response, jsonify, request

from ...zone2 import conversations
from ...zone2.errors import BrainError
from ..auth import PROGRAM
from ..routing import route
from .chat import brain, refused

bp = Blueprint("openai_api", __name__)
LANE = "api-tools"
MODEL = "puffpai"
FALLBACK_CONVERSATION = "api"
NO_MESSAGES = "No messages provided"
NO_USER_MESSAGE = "No user message found"


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _text_of(content) -> str:
    """A message's content as text: a string, or OpenAI's list of parts (the text parts joined)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content
                       if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


def question_of(messages) -> str | None:
    """v1's ``_convert_openai_messages_to_history``: system messages dropped, the last user message is the question.
    Earlier turns are not returned: the Brain keeps its own history per conversation."""
    kept = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"] if isinstance(messages, list) else []
    for m in reversed(kept):
        if m.get("role") == "user":
            return _text_of(m.get("content")).strip()
    return None


def conversation_of(body: dict) -> str:
    """The body's conversation_id, else user (Home Assistant sends its conversation id there), else "api". An id
    the store can't use becomes a short hash of it that it can (letters and digits), never a refusal."""
    raw = body.get("conversation_id") or body.get("user") or FALLBACK_CONVERSATION
    raw = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)
    if conversations.valid_id(raw):
        return raw
    return "api" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]


def _answer(got: dict) -> str:
    return str(got.get("text") or "") or str(got.get("notice") or "")


def _rest(said: str, got: dict) -> str:
    """What the final answer adds after the words already streamed (all of it when none came; a first-chat question
    the brain adds after the model's words). Nothing when the streamed words are not its start."""
    final = _answer(got)
    return final[len(said):] if final.startswith(said) else ""


def _changed(got: dict) -> list:
    return [c.get("tool") for c in got.get("changed") or [] if isinstance(c, dict) and c.get("tool")]


def _start(question: str, conversation: str):
    """Run one turn on a worker thread, its events on a queue (``chat.chat_stream``'s pattern). Returns the queue,
    the cancel flag and the first event, so a refusal before any word can still answer 503."""
    b = brain()
    events: "queue.Queue[dict | None]" = queue.Queue()
    cancelled = threading.Event()

    def work() -> None:
        try:
            b.turn(question, conversation=conversation, emit=events.put, cancelled=cancelled, personal=False)
        except BrainError as exc:
            events.put({"type": "error", "error": exc.message, "state": exc.code, "refused": exc})
        except Exception as exc:  # noqa: BLE001 - the caller gets a plain line
            events.put({"type": "error", "error": f"Something went wrong: {type(exc).__name__}", "state": "error"})
        finally:
            events.put(None)
    threading.Thread(target=work, daemon=True, name="openai-turn").start()
    return events, cancelled, events.get()


def _events(events, cancelled, first):
    """The turn's events, the first one included, until the worker ends; a closed stream cancels the turn."""
    try:
        event = first
        while event is not None:
            yield event
            event = events.get()
    finally:
        cancelled.set()


# -- OpenAI ----------------------------------------------------------------------------------------------------------

@built("GET", "/openai/v1/models", PROGRAM)
def openai_models():
    return jsonify({"object": "list",
                    "data": [{"id": MODEL, "object": "model", "created": int(time.time()), "owned_by": "puffpai-local",
                              "permission": [], "root": MODEL, "parent": None}]})


def _openai_error(message: str, status: int = 400):
    return jsonify(error={"message": message}), status


@built("POST", "/openai/v1/chat/completions", PROGRAM)
def openai_chat_completions():
    body = _body()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _openai_error(NO_MESSAGES)
    question = question_of(messages)
    if not question:
        return _openai_error(NO_USER_MESSAGE)
    conversation = conversation_of(body)
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if not body.get("stream"):
        try:
            got = brain().turn(question, conversation=conversation, personal=False)
        except BrainError as exc:
            return refused(exc)
        except ValueError as exc:
            return _openai_error(str(exc))
        return jsonify({"id": cmpl_id, "object": "chat.completion", "created": created, "model": MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": _answer(got)},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    events, cancelled, first = _start(question, conversation)
    if first is not None and first.get("type") == "error" and first.get("refused") is not None:
        cancelled.set()
        return refused(first["refused"])

    def chunk(delta: dict, finish, meta: dict | None = None) -> str:
        out = {"id": cmpl_id, "object": "chat.completion.chunk", "created": created, "model": MODEL,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if meta is not None:
            out["puffpai_meta"] = meta
        return f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

    def lines():
        said = ""
        meta = None
        for event in _events(events, cancelled, first):
            kind = event.get("type")
            if kind == "token" and event.get("text"):
                said += str(event["text"])
                yield chunk({"content": str(event["text"])}, None)
            elif kind == "done":
                if _rest(said, event):
                    yield chunk({"content": _rest(said, event)}, None)
                meta = {"changed": _changed(event), "notice": event.get("notice") or None}
            elif kind == "error":
                meta = {"error": str(event.get("error") or "")}
        yield chunk({}, "stop", meta or {"changed": [], "notice": None})
        yield "data: [DONE]\n\n"
    return Response(lines(), mimetype="text/event-stream")


# -- Ollama ----------------------------------------------------------------------------------------------------------

@built("GET", "/ollama/api/tags", PROGRAM)
def ollama_tags():
    return jsonify({"models": [{"name": MODEL, "model": MODEL, "modified_at": "2026-02-28T00:00:00Z", "size": 0,
                                "digest": MODEL,
                                "details": {"parent_model": "", "format": "gguf", "family": MODEL, "families": [MODEL],
                                            "parameter_size": "8B", "quantization_level": "Q4_0"}}]})


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ollama_done(content: str, created_at: str) -> dict:
    return {"model": MODEL, "created_at": created_at, "message": {"role": "assistant", "content": content},
            "done": True, "total_duration": 0, "load_duration": 0, "prompt_eval_count": 0, "eval_count": 0}


@built("POST", "/ollama/api/chat", PROGRAM)
def ollama_chat():
    body = _body()
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return jsonify(error=NO_MESSAGES), 400
    question = question_of(messages)
    if not question:
        return jsonify(error=NO_USER_MESSAGE), 400
    conversation = conversation_of(body)
    created_at = _now_iso()

    if not body.get("stream"):                  # v1's rule: whole unless stream is asked for
        try:
            got = brain().turn(question, conversation=conversation, personal=False)
        except BrainError as exc:
            return refused(exc)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(_ollama_done(_answer(got), created_at))

    events, cancelled, first = _start(question, conversation)
    if first is not None and first.get("type") == "error" and first.get("refused") is not None:
        cancelled.set()
        return refused(first["refused"])

    def line(obj: dict) -> str:
        return json.dumps(obj, ensure_ascii=False) + "\n"

    def lines():
        said = ""
        last = None
        for event in _events(events, cancelled, first):
            kind = event.get("type")
            if kind == "token" and event.get("text"):
                said += str(event["text"])
                yield line({"model": MODEL, "created_at": _now_iso(),
                            "message": {"role": "assistant", "content": str(event["text"])}, "done": False})
            elif kind == "done" and _rest(said, event):
                yield line({"model": MODEL, "created_at": _now_iso(),
                            "message": {"role": "assistant", "content": _rest(said, event)}, "done": False})
            elif kind == "error":
                last = str(event.get("error") or "")
        end = _ollama_done("", _now_iso())
        if last is not None:
            end["error"] = last
        yield line(end)
    return Response(lines(), mimetype="application/x-ndjson")
