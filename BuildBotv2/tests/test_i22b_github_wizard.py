"""The GitHub key in the wizard (lane I22b, D93).

The toolsmith step asks for her GitHub key right after Toolsmith's model (and its key, for a cloud model), so
Toolsmith has both from the start. Enter (an empty box on the Desktop) skips it. A key is checked like the
Desktop's GitHub block checks it (``github_key_ok``), then kept in the vault as ``toolsmith.github`` (the inbox
while the vault is locked): the same name Toolsmith's own GitHub search reads, and nothing else lends it.
It is typed hidden, never said back, never in wizard.json.

Fake keys only, no network, no real GitHub, no bot port.
"""

from __future__ import annotations

import json
import logging

import pytest

from wizard_kit import KEY, Kit, TerminalRun
from puffpai_core.api.groups.github import BAD_SHAPE_LINE
from puffpai_core.wizard import lines as L
from puffpai_core.zone3.toolsmith_host.service import GITHUB_KEY_NAME

FAKE_GH = "github_pat_" + "0Fake" * 8            # never a real key; checked never to come back
PIN = "Tq7#mz2Lp!"                                 # a made-up test PIN (tests/z1kit.py), nobody's real one


def _wizard_json(state) -> str:
    p = state / "settings" / "wizard.json"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _never_said(screens, state, key=FAKE_GH):
    for s in screens:
        assert key not in json.dumps(s)
    assert key not in _wizard_json(state)


def test_the_step_asks_for_it_hidden_optional_and_in_plain_words(state):
    kit = Kit(state, vault="open")
    screen = kit.engine().submit("toolsmith", {"where": "local"}, renderer="terminal")
    f = screen["field"]
    assert screen["status"] == "ask" and f["id"] == "github"
    assert f["hidden"] is True and f["required"] is False
    assert f["hint"] == "What you type is hidden. Press Enter to skip."
    assert "30 searches a minute instead of 10" in f["help"] and "stays in your vault" in f["help"]
    browser = kit.engine().submit("toolsmith", {}, renderer="browser")["field"]
    assert browser["id"] == "github" and browser["hint"] == "Leave it empty to skip."


@pytest.mark.parametrize("vault, where", [("open", "vault"), ("locked", "inbox")])
def test_a_key_goes_to_the_vault_as_toolsmith_github_and_is_never_said(state, vault, where):
    kit = Kit(state, vault=vault)
    eng = kit.engine()
    first = eng.submit("toolsmith", {"where": "local"})
    screen = eng.submit("toolsmith", {"github": FAKE_GH})
    assert screen["status"] in ("done", "waiting"), screen
    kept = kit.vault.secrets if where == "vault" else kit.vault.inbox
    assert kept == {GITHUB_KEY_NAME: FAKE_GH}
    assert {"field": "github", "label": "Your GitHub key (optional)", "shown": L.SHOWN_SECRET} in screen["answered"]
    assert any("only to its own GitHub searches" in line for line in screen["lines"])
    _never_said([first, screen], state)


def test_enter_skips_it_and_nothing_is_kept(state):
    kit = Kit(state, vault="open")
    eng = kit.engine()
    eng.submit("toolsmith", {"where": "local"})
    screen = eng.submit("toolsmith", {"github": ""})
    assert screen["status"] in ("done", "waiting"), screen
    assert kit.vault.secrets == {} and kit.vault.inbox == {}
    assert {"field": "github", "label": "Your GitHub key (optional)", "shown": L.SHOWN_SKIPPED} in screen["answered"]
    assert any(line.startswith("No GitHub key was added now.") and "10 searches a minute" in line
               for line in screen["lines"])


def test_a_key_without_a_token_shape_is_asked_again_and_never_kept_or_echoed(state):
    kit = Kit(state, vault="open")
    eng = kit.engine()
    eng.submit("toolsmith", {"where": "local"})
    bad = "ghp_" + "x" * 12 + " " + FAKE_GH          # a space: not a token's shape
    screen = eng.submit("toolsmith", {"github": bad})
    assert screen["status"] == "ask" and screen["field"]["id"] == "github"
    assert screen["error"].startswith(BAD_SHAPE_LINE) and "Enter to skip" in screen["error"]
    assert kit.vault.secrets == {}
    _never_said([screen], state, key=bad)
    _never_said([screen], state)
    assert eng.submit("toolsmith", {"github": ""})["status"] in ("done", "waiting")


def test_with_no_vault_it_is_not_asked_and_says_why(state):
    kit = Kit(state, vault="missing")
    screen = kit.engine().submit("toolsmith", {"where": "local"})
    assert screen["status"] in ("done", "waiting") and screen.get("field") is None
    assert any("can't keep a GitHub key" in line for line in screen["lines"])


def test_a_cloud_model_gets_both_keys_from_the_start(state):
    kit = Kit(state, vault="open")
    eng = kit.engine()
    eng.submit("toolsmith", {"where": "cloud", "provider": "anthropic"})
    screen = eng.submit("toolsmith", {"consent": "yes", "key": KEY, "cost_cap": ""})
    assert screen["status"] == "ask" and screen["field"]["id"] == "github"     # right after the model's questions
    screen = eng.submit("toolsmith", {"github": FAKE_GH})
    assert screen["status"] == "done", screen
    assert kit.vault.secrets == {"toolsmith.anthropic": KEY, GITHUB_KEY_NAME: FAKE_GH}
    _never_said([screen], state)
    _never_said([screen], state, key=KEY)


def test_the_terminal_reads_it_hidden_and_never_prints_it(state):
    run = TerminalRun(Kit(state, vault="open"))
    screen = run.step("toolsmith", {"where": "1", "github": FAKE_GH})
    assert screen["status"] in ("done", "waiting"), screen
    assert "github" in run.hidden_reads
    out = run.all_text()
    assert FAKE_GH not in out and "Your GitHub key (optional)" in out
    assert "What you type is hidden. Press Enter to skip." in out
    assert run.kit.vault.secrets == {GITHUB_KEY_NAME: FAKE_GH}


def test_with_the_real_vault_the_desktops_github_block_sees_it_linked(state, caplog):
    """The real Archivist and the I18g route: what the wizard keeps is what Toolsmith's search reads."""
    caplog.set_level(logging.DEBUG)
    from puffpai_core.api.app import create_app, wire_core
    from puffpai_core.wizard import Engine
    from puffpai_core.wizard.services import default_services
    from puffpai_core.zone1 import archivist
    app = create_app(state)
    core = wire_core(app)
    events: list = []
    core.bus.subscribe("*", events.append, audience="core")
    core.vault.create("here", None, PIN)
    eng = Engine(core.state, default_services(core), registry=core.registry, bus=core.bus)
    screens = [eng.submit("toolsmith", {"where": "local"})]
    assert screens[-1]["field"]["id"] == "github"
    screens.append(eng.submit("toolsmith", {"github": FAKE_GH}))
    assert archivist.for_state(state).vault.meta(GITHUB_KEY_NAME) is not None
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    got = app.test_client().get("/v1/github/key", headers=owner)
    assert got.json["linked"] is True and got.json["vault_name"] == GITHUB_KEY_NAME
    _never_said(screens, state)
    assert FAKE_GH not in repr(events) and FAKE_GH not in caplog.text
    for p in state.rglob("*"):
        if p.is_file():
            assert FAKE_GH.encode() not in p.read_bytes(), p
