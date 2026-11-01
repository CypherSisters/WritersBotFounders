"""Integration lane I1: the brain helpers lane L6 (Toolsmith) calls: toolsmith_turn, ask_json, answer."""

import pytest

from puffpai_core.zone2 import brain as brain_mod
from puffpai_core.zone2.errors import BrainError
from zone2_doubles import FakeRuntime, GateDouble, make_brain

KEY = "sk-" + "Q7wErTy9UiOp3AsDfGhJ"


def tools():
    return [{"name": "download", "description": "Fetch the app's code.",
             "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}}}]


def test_toolsmith_turn_speaks_the_neutral_shape_both_ways(state, freshtable):
    rt = FakeRuntime({"tool_calls": [{"id": "c1", "name": "download", "arguments": {"url": "https://x.test/a"}}],
                      "usage": {"prompt_tokens": 12, "completion_tokens": 3}})
    brain, _items, _reg = make_brain(state, freshtable, rt)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Install the app."}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Reading."},
                                          {"type": "tool_use", "id": "t0", "name": "server_log", "input": {}},
                                          {"type": "opaque", "data": "signed"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t0", "content": "port 3000",
                                      "is_error": False}]},
    ]
    got = brain.toolsmith_turn("You are Toolsmith.", messages, tools())
    assert got == {"content": [{"type": "tool_use", "id": "c1", "name": "download",
                                "input": {"url": "https://x.test/a"}}],
                   "stop": "tool_use", "usage": {"in": 12, "out": 3, "cache_read": 0, "cache_write": 0},
                   "error": None, "retry_after_s": None}
    sent = rt.calls[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool"]
    assert sent[2]["tool_calls"] == [{"function": {"name": "server_log", "arguments": {}}}]
    assert sent[3] == {"role": "tool", "content": "port 3000", "tool_name": "server_log"}
    assert "signed" not in str(sent)                                  # opaque blocks are a cloud company's
    assert rt.calls[0]["tools"][0]["function"]["name"] == "download"


def test_toolsmith_turn_plain_answer_and_length(state, freshtable):
    rt = FakeRuntime({"text": "It runs on port 3000.", "done_reason": "stop"}, {"text": "cut", "done_reason": "length"})
    brain, _i, _r = make_brain(state, freshtable, rt)
    first = brain.toolsmith_turn("s", [{"role": "user", "content": "go"}], [])
    assert first["content"] == [{"type": "text", "text": "It runs on port 3000."}] and first["stop"] == "end_turn"
    assert rt.calls[0]["tools"] is None
    assert brain.toolsmith_turn("s", [{"role": "user", "content": "go"}], [])["stop"] == "max_tokens"


def test_held_values_and_keys_never_reach_the_model_even_with_the_vault_open(state, freshtable):
    rt = FakeRuntime({"text": f"Call 555-1234 with {KEY}"})
    brain, _i, _r = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True))
    got = brain.toolsmith_turn("system", [{"role": "user", "content": [
        {"type": "text", "text": f"README: phone 555-1234, token {KEY}"}]}], [])
    seen = rt.seen(0)
    assert "555-1234" not in seen and KEY not in seen                 # handles only: outside words, vault open
    assert "555-1234" not in got["content"][0]["text"] and KEY not in got["content"][0]["text"]


def test_ask_json_parses_or_says_none(state, freshtable):
    rt = FakeRuntime({"text": '```json\n{"phrases": ["world map"]}\n```'}, {"text": "I can't say."})
    brain, _i, _r = make_brain(state, freshtable, rt)
    assert brain.ask_json("Give search phrases.", '{"goal": "maps"}') == {"phrases": ["world map"]}
    assert "JSON only" in rt.calls[0]["messages"][0]["content"]
    assert brain.ask_json("Give search phrases.", "{}") is None


def test_answer_uses_only_the_given_text(state, freshtable):
    rt = FakeRuntime({"text": " It shows a world map. "})
    brain, _i, _r = make_brain(state, freshtable, rt)
    assert brain.answer("What does it show?", "A world map with live events.") == "It shows a world map."
    assert "A world map with live events." in rt.seen(0) and "Question: What does it show?" in rt.seen(0)


def test_the_floor_still_holds(state, freshtable):
    rt = FakeRuntime({"text": "never"})
    brain, _i, _r = make_brain(state, freshtable, rt, settings={"address": "http://93.184.216.34:11434"})
    for call in (lambda: brain.toolsmith_turn("s", [], []), lambda: brain.ask_json("s", "u"),
                 lambda: brain.answer("q", "c")):
        with pytest.raises(BrainError):
            call()                                                    # X12: a runtime on the internet
    assert rt.calls == []


def test_module_ports_use_the_running_brain(state, freshtable, monkeypatch):
    rt = FakeRuntime({"text": "{\"ok\": true}"}, {"text": "fine"}, {"text": "done"})
    brain, _i, _r = make_brain(state, freshtable, rt)
    monkeypatch.setattr(brain_mod, "_running", brain)
    assert brain_mod.ask_json("s", "u") == {"ok": True}
    assert brain_mod.answer("q", "c") == "fine"
    assert brain_mod.toolsmith_turn("s", [{"role": "user", "content": "go"}], [])["content"][0]["text"] == "done"
    from puffpai_core import seams
    for name in ("zone2.brain.toolsmith_turn", "zone2.brain.ask_json", "zone2.brain.answer"):
        assert seams.find(name) is not None                           # how lane L6's ports.py finds them


def test_lane_l6_ports_reach_the_brain_with_no_not_built(state, freshtable, monkeypatch):
    """With L6 merged: Toolsmith's real Core-side adapter (``toolsmith_host.ports.Ports``) runs its local-model
    turn, its JSON ask and its answer on zone2.brain (no NotBuilt, no None)."""
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    rt = FakeRuntime({"text": "done"}, {"text": "{\"rank\": [1]}"}, {"text": "It shows headlines."})
    brain, _i, _r = make_brain(state, freshtable, rt)
    monkeypatch.setattr(brain_mod, "_running", brain)
    ports = Ports(state)
    turn = ports.local_turn("You are Toolsmith.", [{"role": "user", "content": [{"type": "text", "text": "go"}]}],
                            tools())
    assert turn["content"] == [{"type": "text", "text": "done"}] and turn["stop"] == "end_turn"
    assert ports.brain_json("Rank these.", "[]") == {"rank": [1]}
    assert ports.brain_answer("What does it show?", "Headlines: rain") == "It shows headlines."
    assert len(rt.calls) == 3                                          # all three went to this computer's model
