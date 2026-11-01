"""Lane L2 LIVE: the real local model adds milk through a fake package MCP tool.

Runs only with ``-m live``, and only when this computer's model server
(address from Ollama's own OLLAMA_HOST, as the Core reads it) ALREADY has a
small tool model. It lists models first; it never downloads or removes a
model and never changes Ollama settings. The gate is a test double.
"""

import pytest

from puffpai_core.zone2 import settings as ms
from puffpai_core.zone2.ollama import OllamaRuntime
from zone2_doubles import ConsentDouble, GateDouble, make_brain

#: Small tool models, smallest first (catalog rows with tools: true).
SMALL_TOOL_MODELS = ["qwen2.5:3b-instruct", "llama3.2:3b", "qwen2.5:7b-instruct", "qwen3.5:9b"]


@pytest.mark.live
def test_live_brain_adds_milk(state, freshtable, request):
    if "live" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("live test: run with -m live")
    address = ms.default_address()
    rt = OllamaRuntime(address, timeout_s=300)
    try:
        here = [m["name"] for m in rt.models()]
    except OSError:
        pytest.skip(f"no model server answers at {address}")
    model = next((m for m in SMALL_TOOL_MODELS if m in here), None)
    if model is None:
        pytest.skip("no small tool model is installed; this test never downloads one")
    consent = ConsentDouble(True)
    brain, items, _ = make_brain(state, freshtable, rt, consent=consent, gate=GateDouble(),
                                 settings={"address": address, "model": model})
    got = brain.turn("Put milk on the shopping list.")
    print(f"\nlive model: {model} at {address}\nanswer: {got['text']!r}\nused: {got['used']}")
    assert [i.lower() for i in items] == ["milk"]
    assert got["changed"] == [{"tool": "freshtable.add_to_list"}]
    assert consent.questions and consent.questions[0]["tool"] == "freshtable.add_to_list"
