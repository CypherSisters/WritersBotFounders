# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/toolsmith_cloud.py (adapted)
# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_cloud_loop.py (rewritten-from)
"""The request and reply mappings the Core runs for data-only frontier providers (D42).

A data-only ``zone3.toolsmith-model`` provider names one ``wire`` here; the
Core turns Toolsmith's neutral request into that company's HTTP request and
the reply back (the two operations of 04 quick start 11, contract 1):

- ``build_request(provider, req) -> {url, headers, body}``. ``req`` is
  ``{model, system, tools: [{name, description, input_schema}], messages:
  [{role, content: [blocks]}], max_tokens, purpose}``; blocks are ``text``,
  ``tool_use {id, name, input}``, ``tool_result {tool_use_id, name, content,
  is_error}`` and ``opaque {data}``. The key is never here: headers hold the
  placeholder ``{key}`` in ``key.format``, which only the Core sender fills.
- ``read_reply(wire, reply) -> {content, stop, usage, error, retry_after_s}``.
  ``content`` holds the derived ``text`` and ``tool_use`` blocks plus ONE
  ``opaque`` block with the company's own reply items, which go back unchanged
  on the next turn (signed thinking, reasoning items, thought signatures).
  ``usage`` is ``{in, out, cache_read, cache_write}`` or None when the reply
  has no usage count (the Core then counts the build as at its cap, X29).

v1 sources: ``security/toolsmith_cloud.py``
(``body_for`` :1083 and the status mapping in ``send`` :1114, adapted) and
``build/toolsmith_cloud_loop.py`` (append-only turns, all tool results
in one turn). The OpenAI and Google mappings are new, written from their
official docs read on 2026-10-22 (function calling, Responses API,
generateContent); none of the three is proven against a live API here.
"""

from __future__ import annotations

import json
from typing import Any

ANTHROPIC_VERSION = "2023-06-01"
WIRES = ("anthropic-messages", "openai-responses", "gemini-generate-content")


def _key_headers(provider: dict) -> dict:
    key = provider["toolsmith_model"]["key"]
    return {key["header"]: key["format"]}


def _host(provider: dict) -> str:
    return provider["toolsmith_model"]["hosts"][0]


def _opaque(content: list) -> list | None:
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "opaque":
            return b.get("data")
    return None


def _text_of(blocks: list) -> str:
    return "\n".join(str(b.get("text") or "") for b in blocks
                     if isinstance(b, dict) and b.get("type") == "text")


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content or "(empty)"
    return json.dumps(content, ensure_ascii=False) if content is not None else "(empty)"


# ── Anthropic Messages ──────────────────────────────────────────────────────

def _anthropic_request(provider: dict, req: dict) -> dict:
    messages = []
    for m in req["messages"]:
        if m["role"] == "assistant":
            raw = _opaque(m["content"])
            if raw is None:
                raw = []
                for b in m["content"]:
                    if b.get("type") == "text":
                        raw.append({"type": "text", "text": b.get("text") or ""})
                    elif b.get("type") == "tool_use":
                        raw.append({"type": "tool_use", "id": b["id"], "name": b["name"],
                                    "input": b.get("input") or {}})
            messages.append({"role": "assistant", "content": raw})
            continue
        blocks = []
        for b in m["content"]:
            if b.get("type") == "text":
                blocks.append({"type": "text", "text": b.get("text") or "(empty)"})
            elif b.get("type") == "tool_result":
                row = {"type": "tool_result", "tool_use_id": b["tool_use_id"],
                       "content": _result_text(b.get("content"))}
                if b.get("is_error"):
                    row["is_error"] = True
                blocks.append(row)
        messages.append({"role": "user", "content": blocks})
    body: dict[str, Any] = {"model": req["model"], "max_tokens": int(req.get("max_tokens") or 4096),
                            "system": req.get("system") or "", "messages": messages,
                            "cache_control": {"type": "ephemeral"}}
    tools = sorted(req.get("tools") or [], key=lambda t: t["name"])
    if tools:
        body["tools"] = [{"name": t["name"], "description": t.get("description") or "",
                          "input_schema": t.get("input_schema") or {"type": "object", "properties": {}}}
                         for t in tools]
    headers = {"content-type": "application/json", "anthropic-version": ANTHROPIC_VERSION,
               **_key_headers(provider)}
    return {"url": f"https://{_host(provider)}/v1/messages", "headers": headers, "body": body}


def _anthropic_reply(data: dict) -> dict:
    raw = data.get("content")
    if not isinstance(raw, list):
        return _odd()
    content: list[dict] = []
    for b in raw:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            content.append({"type": "text", "text": str(b.get("text") or "")})
        elif b.get("type") == "tool_use":
            content.append({"type": "tool_use", "id": str(b.get("id") or ""), "name": str(b.get("name") or ""),
                            "input": b.get("input") if isinstance(b.get("input"), dict) else {}})
    content.append({"type": "opaque", "data": raw})
    reason = data.get("stop_reason")
    stop = {"tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}.get(reason, "end_turn")
    u = data.get("usage")
    usage = None
    if isinstance(u, dict) and "input_tokens" in u and "output_tokens" in u:
        usage = {"in": _n(u.get("input_tokens")), "out": _n(u.get("output_tokens")),
                 "cache_read": _n(u.get("cache_read_input_tokens")),
                 "cache_write": _n(u.get("cache_creation_input_tokens"))}
    return {"content": content, "stop": stop, "usage": usage, "error": None, "retry_after_s": None}


# ── OpenAI Responses ────────────────────────────────────────────────────────

def _openai_request(provider: dict, req: dict) -> dict:
    items: list = []
    for m in req["messages"]:
        if m["role"] == "assistant":
            raw = _opaque(m["content"])
            if raw is not None:
                items.extend(raw)
                continue
            text = _text_of(m["content"])
            if text:
                items.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    items.append({"type": "function_call", "call_id": b["id"], "name": b["name"],
                                  "arguments": json.dumps(b.get("input") or {})})
            continue
        text = _text_of(m["content"])
        if text:
            items.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
        for b in m["content"]:
            if b.get("type") == "tool_result":
                items.append({"type": "function_call_output", "call_id": b["tool_use_id"],
                              "output": _result_text(b.get("content"))})
    body: dict[str, Any] = {"model": req["model"], "instructions": req.get("system") or "",
                            "input": items, "max_output_tokens": int(req.get("max_tokens") or 4096),
                            "store": False, "include": ["reasoning.encrypted_content"]}
    tools = sorted(req.get("tools") or [], key=lambda t: t["name"])
    if tools:
        body["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description") or "",
                          "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}
                         for t in tools]
    headers = {"content-type": "application/json", **_key_headers(provider)}
    return {"url": f"https://{_host(provider)}/v1/responses", "headers": headers, "body": body}


def _openai_reply(data: dict) -> dict:
    raw = data.get("output")
    if not isinstance(raw, list):
        return _odd()
    content: list[dict] = []
    refused = False
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            try:
                args = json.loads(item.get("arguments") or "{}")
            except ValueError:
                args = {}
            content.append({"type": "tool_use", "id": str(item.get("call_id") or ""),
                            "name": str(item.get("name") or ""), "input": args if isinstance(args, dict) else {}})
        elif item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    content.append({"type": "text", "text": str(part.get("text") or "")})
                elif isinstance(part, dict) and part.get("type") == "refusal":
                    refused = True
    content.append({"type": "opaque", "data": raw})
    reason = ((data.get("incomplete_details") or {}).get("reason")
              if data.get("status") == "incomplete" else None)
    if refused or reason == "content_filter":
        stop = "refusal"
    elif any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    elif reason == "max_output_tokens":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    u = data.get("usage")
    usage = None
    if isinstance(u, dict) and "input_tokens" in u and "output_tokens" in u:
        cached = _n((u.get("input_tokens_details") or {}).get("cached_tokens"))
        usage = {"in": max(0, _n(u.get("input_tokens")) - cached), "out": _n(u.get("output_tokens")),
                 "cache_read": cached, "cache_write": 0}
    return {"content": content, "stop": stop, "usage": usage, "error": None, "retry_after_s": None}


# ── Google generateContent ──────────────────────────────────────────────────

def _gemini_request(provider: dict, req: dict) -> dict:
    contents: list = []
    for m in req["messages"]:
        if m["role"] == "assistant":
            raw = _opaque(m["content"])
            if raw is not None:
                contents.append(raw)
                continue
            parts = [{"text": b.get("text") or ""} for b in m["content"] if b.get("type") == "text"]
            parts += [{"functionCall": {"name": b["name"], "args": b.get("input") or {}}}
                      for b in m["content"] if b.get("type") == "tool_use"]
            contents.append({"role": "model", "parts": parts})
            continue
        parts = []
        for b in m["content"]:
            if b.get("type") == "text":
                parts.append({"text": b.get("text") or "(empty)"})
            elif b.get("type") == "tool_result":
                fr: dict[str, Any] = {"name": b.get("name") or "",
                                      "response": {"result": _result_text(b.get("content"))}}
                if not str(b["tool_use_id"]).startswith("call_"):
                    fr["id"] = b["tool_use_id"]
                parts.append({"functionResponse": fr})
        contents.append({"role": "user", "parts": parts})
    body: dict[str, Any] = {"contents": contents,
                            "generationConfig": {"maxOutputTokens": int(req.get("max_tokens") or 4096)}}
    if req.get("system"):
        body["systemInstruction"] = {"parts": [{"text": req["system"]}]}
    tools = sorted(req.get("tools") or [], key=lambda t: t["name"])
    if tools:
        body["tools"] = [{"functionDeclarations": [
            {"name": t["name"], "description": t.get("description") or "",
             "parameters": t.get("input_schema") or {"type": "object", "properties": {}}} for t in tools]}]
    headers = {"content-type": "application/json", **_key_headers(provider)}
    model = str(req["model"])
    return {"url": f"https://{_host(provider)}/v1beta/models/{model}:generateContent",
            "headers": headers, "body": body}


def _gemini_reply(data: dict) -> dict:
    if (data.get("promptFeedback") or {}).get("blockReason"):
        return {"content": [], "stop": "refusal", "usage": _gemini_usage(data), "error": None,
                "retry_after_s": None}
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands or not isinstance(cands[0], dict):
        return _odd()
    raw = cands[0].get("content") if isinstance(cands[0].get("content"), dict) else {"role": "model", "parts": []}
    content: list[dict] = []
    for i, part in enumerate(raw.get("parts") or []):
        if not isinstance(part, dict):
            continue
        if "functionCall" in part and isinstance(part["functionCall"], dict):
            fc = part["functionCall"]
            content.append({"type": "tool_use", "id": str(fc.get("id") or f"call_{i}"),
                            "name": str(fc.get("name") or ""),
                            "input": fc.get("args") if isinstance(fc.get("args"), dict) else {}})
        elif "text" in part and not part.get("thought"):
            content.append({"type": "text", "text": str(part.get("text") or "")})
    content.append({"type": "opaque", "data": raw})
    reason = str(cands[0].get("finishReason") or "STOP")
    if reason in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"):
        stop = "refusal"
    elif any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    elif reason == "MAX_TOKENS":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    return {"content": content, "stop": stop, "usage": _gemini_usage(data), "error": None,
            "retry_after_s": None}


def _gemini_usage(data: dict) -> dict | None:
    u = data.get("usageMetadata")
    if not isinstance(u, dict) or "promptTokenCount" not in u:
        return None
    cached = _n(u.get("cachedContentTokenCount"))
    return {"in": max(0, _n(u.get("promptTokenCount")) - cached),
            "out": _n(u.get("candidatesTokenCount")) + _n(u.get("thoughtsTokenCount")),
            "cache_read": cached, "cache_write": 0}


# ── shared ─────────────────────────────────────────────────────────────────

def _n(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _odd() -> dict:
    return {"content": [], "stop": "end_turn", "usage": None, "error": "odd_reply", "retry_after_s": None}


_BUILD = {"anthropic-messages": _anthropic_request, "openai-responses": _openai_request,
          "gemini-generate-content": _gemini_request}
_READ = {"anthropic-messages": _anthropic_reply, "openai-responses": _openai_reply,
         "gemini-generate-content": _gemini_reply}


def build_request(provider: dict, req: dict) -> dict:
    wire = provider["toolsmith_model"].get("wire")
    if wire not in _BUILD:
        raise ValueError("This provider names no mapping the Core runs, so it needs a Verified adapter.")
    return _BUILD[wire](provider, req)


def read_reply(wire: str, reply: dict) -> dict:
    """The status mapping (v1 ``send``): 401/403 key, 429/5xx busy, 413 too big, other 4xx refused."""
    status = int(reply.get("status") or 0)
    retry = None
    try:
        retry = float((reply.get("headers") or {}).get("retry-after") or
                      (reply.get("headers") or {}).get("Retry-After") or "")
    except (TypeError, ValueError):
        retry = None
    if status in (401, 403):
        return {**_odd(), "error": "key"}
    if status == 429 or status >= 500:
        return {**_odd(), "error": "busy", "retry_after_s": retry}
    if status == 413:
        return {**_odd(), "error": "too_big"}
    if status != 200:
        return {**_odd(), "error": "refused"}
    body = reply.get("body")
    try:
        data = body if isinstance(body, dict) else json.loads(body or b"")
    except ValueError:
        return _odd()
    if not isinstance(data, dict):
        return _odd()
    return _READ[wire](data)


__all__ = ["WIRES", "build_request", "read_reply"]
