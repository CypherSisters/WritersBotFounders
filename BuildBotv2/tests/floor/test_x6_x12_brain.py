"""Floor X6 (Zone 2 side) and X12: the gate runs before the brain; keys never reach it; the brain is local.

X6: seeded values become handles in the chat store; keys never reach the
model, vault open or locked; no gate means no turn (fail closed).
X12: a runtime on a non-local address is refused for the brain.
"""

import pytest

from puffpai_core.zone2 import settings as ms
from puffpai_core.zone2.errors import BrainError
from puffpai_core.zone2.gate import production
from zone2_doubles import FakeRuntime, GateDouble, make_brain

KEY = "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2"
PHONE = "555-0134"


@pytest.mark.parametrize("vault_open", [False, True])
def test_x6_seeded_values_and_keys(state, freshtable, vault_open):
    rt = FakeRuntime({"text": f"Got it, {PHONE}."})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=vault_open))
    brain.turn(f"my number is {PHONE} and my key is {KEY}")
    seen = rt.seen(0)
    assert KEY not in seen
    assert (PHONE in seen) is vault_open
    stored = (state / "chat" / "main.jsonl").read_text(encoding="utf-8")
    assert KEY not in stored and PHONE not in stored


def test_x6_no_gate_no_turn(state, freshtable):
    rt = FakeRuntime({"text": "x"})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain._gate = production
    with pytest.raises(BrainError):
        brain.turn("hello")
    assert rt.calls == [] and not (state / "chat" / "main.jsonl").exists()


@pytest.mark.parametrize("address", ["http://8.8.8.8:11434", "https://ollama.example.com", "http://10.0.0.9:11434"])
def test_x12_non_local_runtime_refused(state, freshtable, address):
    rt = FakeRuntime({"text": "x"})
    brain, _, _ = make_brain(state, freshtable, rt, settings={"address": address})
    with pytest.raises(BrainError) as err:
        brain.turn("hello")
    assert err.value.message == ms.NOT_LOCAL and rt.calls == []
