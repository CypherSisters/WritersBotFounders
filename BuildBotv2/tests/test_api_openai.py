"""Lane api-tools: the OpenAI-shaped and Ollama-shaped chat routes through the Flask test client, called with a
program key (``puffpai apikey``), as Home Assistant does. Who else may call them: tests/test_program_keys.py."""

import json

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.auth import PROGRAM
from puffpai_core.zone2 import conversations
from puffpai_core.zone2.errors import BrainError
from zone2_doubles import FakeRuntime, call, make_brain


class StubBrain:
    """A Brain with only ``turn``: it says ``tokens``, then ``done``; or raises ``error`` before any word."""

    def __init__(self, tokens=("Hello", " there."), changed=(), error=None, mid_error=None):
        self.tokens, self.changed, self.error, self.mid_error = list(tokens), list(changed), error, mid_error
        self.calls = []

    def turn(self, text, *, conversation="main", emit=None, cancelled=None, personal=False):
        self.calls.append({"text": text, "conversation": conversation, "personal": personal})
        if self.error is not None:
            raise self.error
        emit = emit or (lambda _e: None)
        emit({"type": "progress", "line": "Thinking."})
        for t in self.tokens:
            emit({"type": "token", "text": t})
        if self.mid_error is not None:
            raise self.mid_error
        text_out = "".join(self.tokens)
        out = {"text": text_out, "empty": not text_out, "notice": None if text_out else "The brain gave no answer.",
               "changed": [{"tool": t} for t in self.changed], "used": [], "steps": 1}
        emit({"type": "done", **out})
        return out


@pytest.fixture
def stub(state):
    def _make(**kw):
        app = create_app(state)
        core = app.extensions["puffpai"]
        b = StubBrain(**kw)
        app.extensions["puffpai_brain"] = b
        owner = {"Authorization": "Bearer " + core.tokens.issue_program_key("home assistant")}
        return app.test_client(), owner, b
    return _make


def sse(r):
    """Every data line parsed as JSON until [DONE]; [DONE] must be the last."""
    assert r.mimetype == "text/event-stream"
    datas = [line[len("data: "):] for line in r.get_data(as_text=True).split("\n\n") if line.strip()]
    assert datas[-1] == "[DONE]"
    return [json.loads(d) for d in datas[:-1]]


def ndjson(r):
    assert r.mimetype == "application/x-ndjson"
    return [json.loads(line) for line in r.get_data(as_text=True).splitlines() if line.strip()]


ASK = {"messages": [{"role": "system", "content": "You are a lamp."}, {"role": "user", "content": "earlier"},
                    {"role": "assistant", "content": "ok"}, {"role": "user", "content": "say hello"}]}


def test_the_four_routes_are_program_routes(state):
    app = create_app(state)
    rules = {r.rule: app.view_functions[r.endpoint].puffpai_access for r in app.url_map.iter_rules()
             if r.rule.startswith(("/openai/", "/ollama/"))}
    assert rules == {"/openai/v1/models": PROGRAM, "/openai/v1/chat/completions": PROGRAM,
                     "/ollama/api/tags": PROGRAM, "/ollama/api/chat": PROGRAM}


def test_model_lists(stub):
    client, owner, _ = stub()
    r = client.get("/openai/v1/models", headers=owner)
    assert r.status_code == 200 and r.json["object"] == "list"
    assert [(m["id"], m["object"], m["owned_by"]) for m in r.json["data"]] == [("puffpai", "model", "puffpai-local")]
    r = client.get("/ollama/api/tags", headers=owner)
    assert r.status_code == 200 and [m["name"] for m in r.json["models"]] == ["puffpai"]


def test_no_credential_is_401(stub):
    client, _owner, b = stub()
    assert client.get("/openai/v1/models").status_code == 401
    assert client.get("/ollama/api/tags").status_code == 401
    assert client.post("/openai/v1/chat/completions", json=ASK).status_code == 401
    assert client.post("/ollama/api/chat", json=ASK).status_code == 401
    assert b.calls == []


def test_completion_shape(stub):
    client, owner, b = stub()
    r = client.post("/openai/v1/chat/completions", headers=owner, json=ASK)
    assert r.status_code == 200
    j = r.json
    assert j["id"].startswith("chatcmpl-") and len(j["id"]) == len("chatcmpl-") + 12
    assert j["object"] == "chat.completion" and j["model"] == "puffpai" and isinstance(j["created"], int)
    assert j["choices"] == [{"index": 0, "message": {"role": "assistant", "content": "Hello there."},
                             "finish_reason": "stop"}]
    assert j["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    # the last user message is the question; system dropped; never her "You" notes
    assert b.calls == [{"text": "say hello", "conversation": "api", "personal": False}]


def test_completion_content_parts(stub):
    client, owner, b = stub()
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "say "}, {"type": "text", "text": "hi"}]}]}
    assert client.post("/openai/v1/chat/completions", headers=owner, json=body).status_code == 200
    assert b.calls[0]["text"] == "say hi"


def test_completion_stream(stub):
    client, owner, _ = stub(tokens=("One", ", two", ", three."), changed=["lights.turn_on"])
    r = client.post("/openai/v1/chat/completions", headers=owner, json={**ASK, "stream": True})
    assert r.status_code == 200
    chunks = sse(r)
    assert all(c["object"] == "chat.completion.chunk" and c["model"] == "puffpai" for c in chunks)
    assert len({c["id"] for c in chunks}) == 1
    deltas = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert deltas == "One, two, three."
    last = chunks[-1]
    assert last["choices"][0] == {"index": 0, "delta": {}, "finish_reason": "stop"}
    assert last["puffpai_meta"] == {"changed": ["lights.turn_on"], "notice": None}
    assert all("puffpai_meta" not in c for c in chunks[:-1])


def test_completion_stream_error_mid_way(stub):
    client, owner, _ = stub(tokens=("Half",), mid_error=BrainError("The model stopped.", "timed_out"))
    r = client.post("/openai/v1/chat/completions", headers=owner, json={**ASK, "stream": True})
    assert r.status_code == 200
    chunks = sse(r)
    assert chunks[0]["choices"][0]["delta"] == {"content": "Half"}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["puffpai_meta"] == {"error": "The model stopped."}


def test_completion_stream_with_the_real_brain(state, freshtable):
    app = create_app(state)
    core = app.extensions["puffpai"]
    brain, _items, _ = make_brain(state, freshtable, FakeRuntime(call("freshtable__show_list"), {"text": "Empty."}))
    brain.registry = core.registry
    from puffpai_core.registry import row_from_manifest
    if core.registry.get("freshtable") is None:
        core.registry.add(row_from_manifest(freshtable, "made", status="running", port=18500))
    app.extensions["puffpai_brain"] = brain
    owner = {"Authorization": "Bearer " + core.tokens.issue_program_key("home assistant")}
    r = app.test_client().post("/openai/v1/chat/completions", headers=owner,
                               json={"messages": [{"role": "user", "content": "show my shopping list"}], "stream": True})
    chunks = sse(r)
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks) == "Empty."
    assert chunks[-1]["puffpai_meta"]["changed"] == []


@pytest.mark.parametrize("body,conversation", [
    ({"user": "01HXYZ-ha_conv"}, "01HXYZ-ha_conv"),
    ({"conversation_id": "kitchen", "user": "other"}, "kitchen"),
    ({}, "api"),
])
def test_conversation_id(stub, body, conversation):
    client, owner, b = stub()
    assert client.post("/openai/v1/chat/completions", headers=owner, json={**ASK, **body}).status_code == 200
    assert b.calls[0]["conversation"] == conversation


@pytest.mark.parametrize("bad", ["has space/and slash", "x" * 65, "-leading", "new\nline"])
def test_bad_conversation_id_is_hashed(stub, bad):
    client, owner, b = stub()
    assert client.post("/openai/v1/chat/completions", headers=owner, json={**ASK, "user": bad}).status_code == 200
    got = b.calls[0]["conversation"]
    assert got != bad and conversations.valid_id(got) and got.isalnum()
    client.post("/openai/v1/chat/completions", headers=owner, json={**ASK, "user": bad})
    assert b.calls[1]["conversation"] == got               # the same id each time: one conversation


@pytest.mark.parametrize("body,line", [
    ({}, "No messages provided"),
    ({"messages": []}, "No messages provided"),
    ({"messages": [{"role": "system", "content": "x"}]}, "No user message found"),
    ({"messages": [{"role": "user", "content": "   "}]}, "No user message found"),
])
def test_400_shapes(stub, body, line):
    client, owner, b = stub()
    r = client.post("/openai/v1/chat/completions", headers=owner, json=body)
    assert r.status_code == 400 and r.json == {"error": {"message": line}}
    r = client.post("/ollama/api/chat", headers=owner, json=body)
    assert r.status_code == 400 and r.json == {"error": line}
    assert b.calls == []


@pytest.mark.parametrize("path", ["/openai/v1/chat/completions", "/ollama/api/chat"])
@pytest.mark.parametrize("stream", [False, True])
def test_503_on_brain_refusal(stub, path, stream):
    client, owner, _ = stub(error=BrainError("No model is set up yet.", "no_model"))
    r = client.post(path, headers=owner, json={**ASK, "stream": stream})
    assert r.status_code == 503 and r.json == {"error": "No model is set up yet.", "state": "no_model"}


def test_ollama_chat(stub):
    client, owner, b = stub()
    r = client.post("/ollama/api/chat", headers=owner, json=ASK)
    assert r.status_code == 200
    j = r.json
    assert j["created_at"].endswith("Z") and j.pop("created_at")
    assert j == {"model": "puffpai", "message": {"role": "assistant", "content": "Hello there."}, "done": True,
                 "total_duration": 0, "load_duration": 0, "prompt_eval_count": 0, "eval_count": 0}
    assert b.calls[0]["text"] == "say hello" and b.calls[0]["personal"] is False


def test_ollama_chat_stream(stub):
    client, owner, _ = stub(tokens=("A", "B", "C"))
    r = client.post("/ollama/api/chat", headers=owner, json={**ASK, "stream": True})
    assert r.status_code == 200
    lines = ndjson(r)
    assert [l["message"]["content"] for l in lines[:-1]] == ["A", "B", "C"]
    assert all(l["done"] is False and l["model"] == "puffpai" and l["message"]["role"] == "assistant"
               for l in lines[:-1])
    end = lines[-1]
    assert end["done"] is True and end["message"] == {"role": "assistant", "content": ""}
    assert end["total_duration"] == 0 and end["eval_count"] == 0
