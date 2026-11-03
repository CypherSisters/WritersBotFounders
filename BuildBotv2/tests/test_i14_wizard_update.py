"""Lane I14: a bot set up from main (before lane I10c) gets the new wizard without a card, and an old step list
still walks through to done.

The verifier's seam: I10c changed the wizard's ``steps`` (the Apps step went, Name and Look came), the wizard
is a system part, and lane I13's update held it behind a Yes card whose "what changed" said nothing. Until
the Yes, the engine read the old list and asked the Apps step's question forever.

The "repo" is a built-ins folder of this test's own (``PUFFPAI_BUILTIN``): first main's wizard (from git, at
dddfb39a), installed; then the copy in this tree swapped in, the way a code update does.
"""

import json
import shutil
import subprocess

import pytest

from conftest import REPO, yes
from puffpai_core.packages import manager
from puffpai_core.wizard import steps as steps_mod

MAIN = "dddfb39a"          # main before lane I10c: its wizard has the Apps step


def _git_show(path: str) -> bytes:
    return subprocess.run(["git", "show", f"{MAIN}:{path}"], cwd=str(REPO), capture_output=True, check=True).stdout


def _has_main() -> bool:
    try:
        return subprocess.run(["git", "cat-file", "-e", MAIN + "^{commit}"], cwd=str(REPO), capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.fixture
def repo(tmp_path, monkeypatch):
    if not _has_main():
        pytest.skip(f"commit {MAIN} is not here (an export with no history), so main's old wizard can't be read")
    base = tmp_path / "builtin"
    (base / "wizard").mkdir(parents=True)
    for name in ("puffpai.json", "README.md"):
        (base / "wizard" / name).write_bytes(_git_show(f"packages/wizard/{name}"))
    monkeypatch.setenv("PUFFPAI_BUILTIN", str(base))
    return base


@pytest.fixture
def wired(core):
    from puffpai_core.api.app import wire_core
    wire_core(core.app)
    core.packages.start_after_install = False
    return core


def _ids(core):
    got, _preset, _wid = steps_mod.load_steps(core.state, core.registry)
    return [s["id"] for s in got]


def test_mains_wizard_updates_silently_to_the_new_steps(wired, repo):
    core = wired
    assert yes(core, core.packages.add("wizard"))["status"] == "installed"
    assert core.steward.manifest("wizard")["version"] == "0.1.0"
    assert "apps" in [s["id"] for s in core.steward.manifest("wizard")["steps"]]
    shutil.rmtree(repo / "wizard")
    shutil.copytree(REPO / "packages" / "wizard", repo / "wizard")      # the code update: this tree's wizard
    got = core.packages.check_builtins(["wizard"])
    assert got[0]["status"] == "updated", got
    assert core.consent.list(status="pending") == []                    # no card holds setup
    want = json.loads((REPO / "packages" / "wizard" / "puffpai.json").read_text(encoding="utf-8"))["version"]
    assert core.steward.manifest("wizard")["version"] == want != "0.1.0"
    ids = _ids(core)
    assert {"name", "look"} <= set(ids) and "apps" not in ids


def test_another_field_of_the_wizard_still_needs_her_yes(wired, repo):
    """Only ``steps`` is added to the wizard's silent fields; its consent text changing still asks."""
    import json
    core = wired
    assert yes(core, core.packages.add("wizard"))["status"] == "installed"
    path = repo / "wizard" / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data.update(version="0.1.1", consent="The setup steps run on this computer, and something more.")
    path.write_text(json.dumps(data), encoding="utf-8")
    got = core.packages.check_builtins(["wizard"])
    assert got[0]["status"] == "card"
    assert got[0]["what_changed"]["fields"] == ["its consent text", "its version"]
    from puffpai_core.consent.shown import view
    lines = view(got[0]["card"])["lines"]                              # her card says what changed
    assert "What changed: its consent text, its version." in lines and "Version: 0.1.0 -> 0.1.1" in lines


def test_steps_are_silent_only_for_the_wizard():
    old = {"id": "desktop", "steps": [1]}
    new = {"id": "desktop", "steps": [2]}
    assert manager.manifest_difference(old, new) == ["steps"]            # unchanged: the allow-list is per part
    assert manager._SILENT_FOR == {"wizard": frozenset({"steps"})}


def test_what_changed_lists_every_field_in_plain_words():
    old = {"id": "x", "version": "1", "run": {"cmd": "a"}, "steps": [], "zany_field": 1}
    new = {"id": "x", "version": "1", "run": {"cmd": "b"}, "steps": [1]}
    assert manager.what_changed(old, new)["fields"] == ["how it starts", "the setup steps it asks", "its zany field"]
    assert manager.what_changed(old, dict(old))["fields"] == ["only its files (the manifest is the same)"]


def test_the_card_shows_what_changed():
    from puffpai_core.consent.shown import view as card_view
    card = {"title": "Update BuildBot setup?", "id": "wizard", "name": "BuildBot setup", "kind": "wizard",
            "hosts": [], "keys": [], "changes": [], "your_turn": [], "drawbridge": {"line": "passed every check."},
            "what_changed": {"version": ["0.1.0", "0.1.0"], "fields": ["the setup steps it asks"]}}
    lines = card_view(card, False)["lines"]
    assert "What changed: the setup steps it asks." in lines


def test_an_old_step_list_with_apps_walks_through_to_done(state, repo):
    """Main's step list (the only wizard in this test's built-ins), read by the engine's own load_steps: Apps
    is skipped, every step left has code, and the terminal walks through to done."""
    import json
    old = json.loads(_git_show("packages/wizard/puffpai.json"))["steps"]
    assert "apps" in [s["id"] for s in old]
    kept = [s["id"] for s in steps_mod.coded_only(old)]
    assert "apps" not in kept and kept[-1] == "done" and set(kept) <= steps_mod.CODED_STEPS
    from wizard_kit import KEY, Kit, TerminalRun, vault_inputs
    kit = Kit(state)
    assert [s["id"] for s in kit.engine().steps] == kept
    run = TerminalRun(kit)
    answers = {**vault_inputs(), "model": "", "posture": "search", "where": ["here", "cloud"], "provider": "anthropic",
               "consent": "yes", "key": KEY, "goal": "a helper for my week", "yes": "yes"}
    run.inputs = {k: (list(v) if isinstance(v, list) else [v]) for k, v in answers.items()}
    assert run.term.run() == 0
    assert kit.engine().overview()["complete"] is True


def test_the_engine_has_code_for_exactly_the_coded_steps(state):
    from wizard_kit import Kit
    assert set(Kit(state).engine().hooks) == steps_mod.CODED_STEPS


def test_a_step_with_no_code_is_skipped_with_a_log_line(caplog):
    with caplog.at_level("INFO", logger="puffpai_core.wizard.steps"):
        kept = steps_mod.coded_only([{"id": "scan"}, {"id": "teleport"}, {"id": "apps"}, {"id": "done"}])
    assert [s["id"] for s in kept] == ["scan", "done"]
    assert "teleport has no code" in caplog.text and "apps is retired" in caplog.text
