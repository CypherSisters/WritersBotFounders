"""Lane I17 LIVE: the plan's one small ask ("does this goal need a coding or picture model?") on this
computer's real model, for two probe goals. One goal is a probe, never the target.

Runs only with ``-m live``, against 127.0.0.1:11435 only, and only when a chat model from the catalog is
ALREADY there. It lists models and sends two small chat asks; it never downloads or removes a model.
"""

import pytest

from puffpai_core.zone2 import roles
from puffpai_core.zone2.brain import JSON_NOTE, read_json
from puffpai_core.zone2.catalog import get_profile
from puffpai_core.zone2.ollama import OllamaRuntime

ADDRESS = "http://127.0.0.1:11435"
CHAT_MODELS = ["qwen3.5:9b", "qwen2.5:7b-instruct", "qwen3:30b-a3b"]


def _ask(rt, model):
    profile = get_profile(model)

    def ask(system, user):
        got = rt.chat([{"role": "system", "content": system + JSON_NOTE}, {"role": "user", "content": user}],
                      model=model, think=bool(profile.get("needs_thinking")),
                      options={"num_ctx": int(profile["budgets"]["num_ctx"])})
        return read_json(got["text"])
    return ask


@pytest.mark.live
def test_live_the_brain_offers_code_only_for_the_coder_bot(request):
    if "live" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("live test: run with -m live")
    rt = OllamaRuntime(ADDRESS, timeout_s=300)
    try:
        here = [m["name"] for m in rt.models()]
    except OSError:
        pytest.skip(f"no model server answers at {ADDRESS}")
    model = next((m for m in CHAT_MODELS if m in here), None)
    if model is None:
        pytest.skip("no catalog chat model is installed; this test never downloads one")
    ask = _ask(rt, model)
    coder = roles.needs(ask, "a coder bot that helps me write Python scripts", [])
    recipes = roles.needs(ask, "a recipe bot", [])
    print(f"\nlive model: {model} at {ADDRESS}\ncoder bot: {coder}\nrecipe bot: {recipes}")
    assert "code" in coder
    assert "code" not in recipes
