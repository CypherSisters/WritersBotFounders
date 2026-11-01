"""Lane L2: the chat, brain and model API groups through the Flask test client."""

import json

import pytest

from puffpai_core.api import create_app
from zone2_doubles import ConsentDouble, FakeRuntime, GateDouble, call, make_brain


@pytest.fixture
def setup(state, freshtable):
    def _make(*script, consent=None, gate=None):
        app = create_app(state)
        core = app.extensions["puffpai"]
        rt = FakeRuntime(*script)
        brain, items, _ = make_brain(state, freshtable, rt, consent=consent, gate=gate)
        brain.registry = core.registry
        if core.registry.get("freshtable") is None:
            core.registry.add(brain_row(freshtable))
        app.extensions["puffpai_brain"] = brain
        owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
        return app.test_client(), owner, core, rt, items
    return _make


def brain_row(freshtable):
    from puffpai_core.registry import row_from_manifest
    return row_from_manifest(freshtable, "made", status="running", port=18500)


def test_chat_route(setup):
    client, owner, _core, _rt, items = setup(call("freshtable__add_to_list", item="milk"), {"text": "Milk is on."},
                                             consent=ConsentDouble(True))
    r = client.post("/v1/chat", headers=owner, json={"text": "put milk on the shopping list"})
    assert r.status_code == 200 and r.json["text"] == "Milk is on." and items == ["milk"]
    assert r.json["changed"] == [{"tool": "freshtable.add_to_list"}]
    assert client.post("/v1/chat", headers=owner, json={}).status_code == 400
    assert client.post("/v1/chat", json={"text": "hi"}).status_code == 401


def test_chat_stream_route(setup):
    client, owner, *_ = setup(call("freshtable__show_list"), {"text": "Empty."})
    r = client.post("/v1/chat/stream", headers=owner, json={"text": "show my shopping list"})
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines() if line.strip()]
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "done" and "tool_done" in kinds and "token" in kinds
    assert events[-1]["text"] == "Empty."


def test_chat_fails_closed_without_the_gate(setup):
    client, owner, core, rt, _ = setup({"text": "never"})
    from puffpai_core.zone1 import gate as l3_gate
    from puffpai_core.zone2.gate import production
    client.application.extensions["puffpai_brain"]._gate = production
    l3_gate.bind(None)                          # the Core binds its Archivist at start; no Archivist, no gate
    r = client.post("/v1/chat", headers=owner, json={"text": "hello"})
    assert r.status_code == 503 and r.json["state"] == "no_gate" and rt.calls == []


@pytest.mark.parametrize("yes,code", [(False, 422), (True, 200)])
def test_chat_tool_route_asks_the_same_yes(setup, yes, code):
    client, owner, _c, _rt, items = setup(consent=ConsentDouble(yes))
    r = client.post("/v1/chat/tool", headers=owner, json={"tool": "freshtable.add_to_list", "args": {"item": "eggs"}})
    assert r.status_code == code and items == (["eggs"] if yes else [])


def test_brain_chat_for_packages(setup):
    client, _owner, core, rt, _ = setup({"text": "Two recipes.", "usage": {"prompt_tokens": 9, "completion_tokens": 3}},
                                        gate=GateDouble(vault_open=True))
    tok = {"Authorization": "Bearer " + core.tokens.issue_package_token("freshtable")}
    r = client.post("/v1/brain/chat", headers=tok, json={"messages": [{"role": "user", "content": "call 555-0134"}]})
    assert r.status_code == 200 and r.json == {"text": "Two recipes.", "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                                         "model": "fake:1b"}
    assert "555-0134" not in rt.seen(0)
    r = client.post("/v1/brain/chat", headers=tok, json={"messages": [{"role": "user", "content": "x"}], "images": ["aGk="]})
    assert r.status_code == 400 and "cannot read pictures" in r.json["error"]


def test_model_routes(setup, monkeypatch):
    client, owner, *_ = setup()
    r = client.get("/v1/model", headers=owner)
    assert r.status_code == 200 and r.json["settings"]["model"] == "fake:1b"
    assert r.json["state"]["state"] == "error"                       # nothing answers on 127.0.0.1:9
    from puffpai_core.zone2 import models
    monkeypatch.setattr(models, "census", lambda _d=None: {"memory": {"total_gb": 16.0}, "disk": {"free_gb": 50.0},
                                                            "hardware_class": "mid"})
    r = client.get("/v1/model/menu", headers=owner)
    assert r.status_code == 200 and r.json["menu"]["recommended"]["id"] == "qwen3.5:9b"
