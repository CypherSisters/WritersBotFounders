"""Integration lane I2a: ``puffpai setup`` adds the built-in packages through the Package Manager, each with
its own Yes card shown in the terminal and answered by her (10_BUILD_PLAN "done" item 1)."""

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.app import wire_core
from puffpai_core.wizard import terminal as term_mod


@pytest.fixture
def core(state):
    core = wire_core(create_app(state))
    core.packages.start_after_install = False        # nothing outlives the test; Steward starts them in a Core
    yield core
    core.steward.stop_all(remember=False)
    if core.zone3 is not None:
        core.zone3.stop()


def test_each_builtin_shows_its_card_and_installs_after_her_yes(core):
    asked, out = [], []
    done = term_mod.builtins(core, lambda prompt: asked.append(prompt) or "y", out.append)
    assert done == list(term_mod.BUILTINS)
    assert len(asked) == len(term_mod.BUILTINS)
    text = "\n".join(out)
    for pid in term_mod.BUILTINS:
        row = core.registry.get(pid)
        assert row is not None and row["status"] in ("stopped", "running")
        card = next(r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": pid})
        assert card["status"] == "granted" and card["by"] == "owner"     # her Yes, on the card shown
    assert text.count("What it is: ") == len(term_mod.BUILTINS) and "Drawbridge" in text
    assert term_mod.builtins(core, lambda prompt: "y", out.append) == []   # installed ones are not asked again


def test_a_no_leaves_that_package_out(core):
    out = []
    answers = iter(["y", "n", "y", "y", "y"])
    done = term_mod.builtins(core, lambda prompt: next(answers), out.append)
    assert "wizard" not in done and core.registry.get("wizard") is None
    assert any("Not added. You can add it later with: puffpai add" in line for line in out)
