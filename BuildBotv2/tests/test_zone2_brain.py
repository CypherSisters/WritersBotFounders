"""Lane L2: the brain loop, Concierge, the gate before Zone 2, consent, the conversation store."""

import pytest

from puffpai_core.zone2 import concierge as cg
from puffpai_core.zone2.errors import BrainError
from puffpai_core.zone2.loop import NO_ANSWER
from zone2_doubles import ConsentDouble, FakeRuntime, GateDouble, call, make_brain


def test_put_milk_on_the_list_adds_milk(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "I put milk on your shopping list."})
    consent = ConsentDouble(True)
    brain, items, _ = make_brain(state, freshtable, rt, consent=consent)
    got = brain.turn("put milk on the shopping list")
    assert items == ["milk"]
    assert got["text"] == "I put milk on your shopping list."
    assert got["changed"] == [{"tool": "freshtable.add_to_list"}]
    assert consent.questions[0]["tool"] == "freshtable.add_to_list" and consent.questions[0]["package"] == "freshtable"
    names = [t["function"]["name"] for t in rt.calls[0]["tools"]]
    assert "freshtable__add_to_list" in names and "list_installed" in names     # <id>__<tool> on the wire
    assert rt.calls[0]["think"] is False                                         # think: false on answer calls
    assert "Added milk" in rt.seen(1)                                            # the tool result came back


def test_a_changes_tool_never_runs_without_a_yes(state, freshtable):
    for consent, line in ((ConsentDouble(False), "You said no"), (ConsentDouble(None), "No answer came"),
                          (None, "no Yes card")):
        rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "ok"})
        brain, items, _ = make_brain(state / str(id(consent)), freshtable, rt, consent=consent)
        got = brain.turn("put milk on the shopping list")
        assert items == [] and got["changed"] == []
        assert line in rt.seen(1)


def test_changes_comes_from_the_registry_not_the_package(state, freshtable):
    # show_list is not in abilities.changes: it runs with no Yes; add_to_list always asks.
    rt = FakeRuntime(call("freshtable__show_list"), {"text": "It is empty."})
    consent = ConsentDouble(True)
    brain, _, _ = make_brain(state, freshtable, rt, consent=consent)
    brain.turn("what is on my shopping list")
    assert consent.questions == []


def test_no_rewriting_of_the_brains_sentences(state, freshtable):
    said = "Milk was not added, because you said no. – Visit /dashboard/apps/freshtable."
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": said})
    brain, items, _ = make_brain(state, freshtable, rt, consent=ConsentDouble(False))
    got = brain.turn("put milk on the shopping list")
    assert got["text"] == said            # no filter rewrites what it said: changed[] tells the truth
    assert got["changed"] == [] and items == []


def test_a_claim_after_her_no_is_asked_again(state, freshtable):
    """Lane I6a: the tool did not run (her No), so "I added milk" is a claim no tool backs: asked once more."""
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "Done! I added milk."},
                     {"text": "Milk was not added, because you said no."})
    brain, items, _ = make_brain(state, freshtable, rt, consent=ConsentDouble(False))
    got = brain.turn("put milk on the shopping list")
    assert got["text"] == "Milk was not added, because you said no." and items == []


def test_an_empty_reply_is_reported_empty(state, freshtable):
    rt = FakeRuntime({"text": "   "})
    brain, _, _ = make_brain(state, freshtable, rt)
    got = brain.turn("hello")
    assert got == {**got, "text": "", "empty": True, "notice": NO_ANSWER}


def test_open_package_mid_turn(state, freshtable):
    rt = FakeRuntime(call("open_package", id="freshtable"), call("freshtable__show_list"), {"text": "Empty."})
    brain, _, _ = make_brain(state, freshtable, rt)
    got = brain.turn("hello there")                   # no word matches FreshTable, so it starts without its tools
    assert "freshtable__show_list" not in [t["function"]["name"] for t in rt.calls[0]["tools"]]
    assert "freshtable__show_list" in [t["function"]["name"] for t in rt.calls[1]["tools"]]
    assert got["used"] == ["open_package", "freshtable.show_list"]


def test_tool_budget_and_core_tools(state, freshtable):
    rt = FakeRuntime({"text": "hi"})
    brain, _, _ = make_brain(state, freshtable, rt, settings={"tool_budget": 9})
    brain.turn("shopping list")
    names = [t["function"]["name"] for t in rt.calls[0]["tools"]]
    assert set(cg.CORE_TOOL_NAMES) <= set(names) and len(names) <= 9


def test_everything_installed_is_in_the_prompt(state, freshtable):
    rt = FakeRuntime({"text": "hi"})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain.turn("hello")
    assert "freshtable: FreshTable (app, running)" in rt.calls[0]["messages"][0]["content"]


def test_vault_locked_the_model_gets_handles(state, freshtable):
    rt = FakeRuntime({"text": "Noted."})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=False))
    brain.turn("call me on 555-0134")
    assert "555-0134" not in rt.seen(0) and "{{vault:r1}}" in rt.seen(0)


def test_vault_open_real_values_to_the_model_handles_in_the_store(state, freshtable):
    rt = FakeRuntime({"text": "I will remember 555-0134."})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True))
    got = brain.turn("call me on 555-0134")
    assert "555-0134" in rt.seen(0)
    assert got["text"] == "I will remember 555-0134."             # her own screen gets the brain's words
    stored = (state / "chat" / "main.jsonl").read_text(encoding="utf-8")
    assert "555-0134" not in stored and stored.count("{{vault:r1}}") == 2


def test_keys_never_reach_the_brain_even_with_the_vault_open(state, freshtable):
    key = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True))
    brain.turn(f"install immich, my key is {key}")
    assert key not in rt.seen(0)
    assert key not in (state / "chat" / "main.jsonl").read_text(encoding="utf-8")


def test_a_gate_that_misses_a_key_is_backstopped(state, freshtable):
    leaky = GateDouble(vault_open=True)
    leaky.prepare = lambda text, open_: (text, [])          # lets everything through
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=leaky)
    brain.turn("my token is ghp_" + "a" * 36)
    assert "ghp_" + "a" * 36 not in rt.seen(0)


def test_tool_results_pass_the_gate_and_arguments_get_handles(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="call 555-0134"), {"text": "ok"})
    brain, items, _ = make_brain(state, freshtable, rt, consent=ConsentDouble(True), gate=GateDouble(vault_open=False))
    brain.turn("put call 555-0134 on the shopping list")
    assert items == ["call {{vault:r1}} (a phone number)"]   # the package got the handle
    assert "555-0134" not in rt.seen(1)                       # the tool result was gated too


def test_no_gate_no_turn(state, freshtable):
    rt = FakeRuntime({"text": "should never be asked"})
    brain, _, _ = make_brain(state, freshtable, rt)
    from puffpai_core.zone2.gate import production
    brain._gate = production                                   # L3 not landed: zone1.gate does not exist
    with pytest.raises(BrainError) as err:
        brain.turn("hello")
    assert err.value.code == "no_gate" and rt.calls == []


def test_a_runtime_on_the_internet_is_refused(state, freshtable):
    rt = FakeRuntime({"text": "nope"})
    brain, _, _ = make_brain(state, freshtable, rt, settings={"address": "http://93.184.216.34:11434"})
    with pytest.raises(BrainError) as err:
        brain.turn("hello")
    assert err.value.code == "not_local" and rt.calls == []


def test_a_home_brain_gets_handles_only(state, freshtable):
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True),
                             settings={"address": "http://192.168.1.50:11434", "lan_hosts": ["192.168.1.50"]})
    brain.turn("call me on 555-0134")
    assert "555-0134" not in rt.seen(0)


def test_one_conversation_with_history(state, freshtable):
    rt = FakeRuntime({"text": "Hi Sam."}, {"text": "Still here."})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain.turn("hello")
    brain.turn("are you there")
    roles = [m["role"] for m in rt.calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_sample_ask_dry_run(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"))
    brain, items, _ = make_brain(state, freshtable, rt)
    got = brain.sample_ask("freshtable.add_to_list", "put milk on the shopping list")
    assert got["ok"] is True and got["called"] == ["freshtable.add_to_list"] and items == []
    assert not (state / "chat" / "main.jsonl").exists()
    rt = FakeRuntime({"text": "Sure, milk."})
    brain, _, _ = make_brain(state / "b", freshtable, rt)
    got = brain.sample_ask("freshtable.add_to_list", "put milk on the shopping list")
    assert got["ok"] is False and "called no tool" in got["reason"]


def test_package_chat_gets_handles_only(state, freshtable):
    rt = FakeRuntime({"text": "Summary."})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True))
    got = brain.package_chat([{"role": "user", "content": "summarize: phone 555-0134"}])
    assert got["text"] == "Summary." and "555-0134" not in rt.seen(0) and rt.calls[0]["tools"] is None


def test_core_tools_with_no_part_here_say_so_plainly(state, freshtable):
    rt = FakeRuntime(call("checklist_add", title="Install Docker", why="FreshTable needs it"),
                     call("search_knowledge", query="soup"), call("ask_toolsmith", job="a news map"),
                     {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain.turn("help")
    assert "Checklist is not here" in rt.seen(1)
    assert "knowledge index is not here" in rt.seen(2)
    assert "Toolsmith is not part of this Core" in rt.seen(3)
