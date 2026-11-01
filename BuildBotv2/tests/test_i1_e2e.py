"""Integration lane I1, end to end on real parts (``-m e2e``): see ``tests/i1_e2e.py`` for the steps.

Needs this computer's model server with the small tool model installed (``settings``: 127.0.0.1:11435,
qwen2.5:7b-instruct); skipped when it is not there. The machine key is conftest's fake (no real DPAPI).
"""

import json
import urllib.request

import pytest

import i1_e2e

pytestmark = pytest.mark.e2e


def _model_here() -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{i1_e2e.ADDRESS}/api/tags", timeout=3) as r:
            return any(m.get("name") == i1_e2e.MODEL for m in json.loads(r.read()).get("models", []))
    except (OSError, ValueError):
        return False


@pytest.mark.skipif(not _model_here(), reason=f"{i1_e2e.MODEL} is not on {i1_e2e.ADDRESS}")
def test_fresh_bot_vault_freshtable_chat_milk_and_drop_ins(state, request):
    if "e2e" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("end-to-end run: run with -m e2e")
    steps = i1_e2e.run(state, log=print)
    failed = [s for s in steps if not s["ok"]]
    assert not failed, "\n".join(f"{s['step']}: {s['said']}" for s in failed)
    assert len(steps) >= 15
