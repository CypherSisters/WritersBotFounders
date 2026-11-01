"""The wizard engine's own rules: show_when, paths, examples, bundles, resume, the full run."""

import json

import pytest

from puffpai_core.wizard import StepListError, apply_bundle, example, expand_path, load_steps, visible
from puffpai_core.wizard.fields import PathError, synced_folder
from wizard_kit import CODE, KEY, Kit, TerminalRun, vault_inputs


def test_visible():
    f = {"id": "folder", "show_when": {"where": ["folder", "existing"]}}
    assert not visible(f, {"where": "here"}) and visible(f, {"where": "existing"}) and not visible(f, {})
    assert visible({"id": "x"}, {})


def test_examples_per_platform():
    f = {"examples": {"windows": "E:\\V", "linux": "/media/v", "jetson_agx_orin_64gb": "/mnt/nvme/v"}}
    assert example(f, "windows") == "E:\\V" and example(f, "linux") == "/media/v" and example(f, "mac") is None
    assert example(f, "linux", board="jetson_agx_orin_64gb") == "/mnt/nvme/v"
    assert example({"examples": "same"}, "mac") == "same"


def test_expand_path():
    env = {"USERPROFILE": "C:\\Users\\sam", "DATA": "D:\\stuff", "HOME": "/home/you"}
    assert expand_path("~\\Vault", "windows", env) == "C:\\Users\\sam\\Vault"
    assert expand_path("%userprofile%\\x", "windows", env) == "C:\\Users\\sam\\x"
    assert expand_path("${DATA}/a/../b", "windows", env) == "D:\\stuff\\b"
    assert expand_path("$HOME/v", "linux", env) == "/home/you/v"
    with pytest.raises(PathError, match="FOO"):
        expand_path("$FOO/v", "linux", env)
    with pytest.raises(PathError):
        expand_path("  ", "linux", env)
    assert synced_folder("C:\\Users\\r\\OneDrive\\Docs") == "OneDrive" and synced_folder("D:\\Data") is None


def test_onedrive_warning(state):
    kit = Kit(state, env={"USERPROFILE": "C:\\Users\\r"})
    eng = kit.engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    screen = eng.submit("vault", {"data_folder": str(state / "OneDrive" / "PuffPAI")})
    assert any("inside OneDrive, which copies it to the internet" in line for line in screen["lines"])


def test_bundle_add_remove_preset(state):
    base, _, _ = load_steps(state)
    steps, preset = apply_bundle(base, {"remove": ["goal"], "preset": {"goal": "A home helper"},
                                        "add": [{"id": "ha", "title": "Connect Home Assistant", "after": "bridge",
                                                 "fields": [{"id": "url", "type": "text", "label": "Address"}]}]})
    ids = [s["id"] for s in steps]
    assert "goal" not in ids and ids[ids.index("bridge") + 1] == "ha" and preset == {"goal": "A home helper"}
    with pytest.raises(StepListError, match="can't remove the vault step"):
        apply_bundle(base, {"remove": ["vault"]})


def test_bundle_step_is_generic(state):
    base, _, _ = load_steps(state)
    steps, _ = apply_bundle(base, {"add": [{"id": "ha", "title": "Connect Home Assistant", "after": "bridge",
                                            "fields": [{"id": "url", "type": "text", "label": "Address"}]}]})
    eng = Kit(state).engine(steps=steps)
    assert eng.submit("ha", {})["field"]["label"] == "Address"
    assert eng.submit("ha", {"url": "http://homeassistant.local:8123"})["status"] == "done"


def test_resume_after_closing(state):
    kit = Kit(state)
    kit.vault._status = "locked"
    eng = kit.engine()
    eng.submit("vault", {"where": "existing"})
    again = kit.engine()                                 # a new window, a new engine: nothing lost
    screen = again.submit("vault", {})
    assert screen["field"]["id"] == "folder" and screen["answered"][0]["shown"] == "A vault I already have"


# -- D109, D111: a vault in a chosen folder, or from another folder, is hidden until v3 --
def saved_answer(kit, where):
    """A bot that picked the choice before it was hidden: the answer is on disk."""
    eng = kit.engine()
    eng._store(eng._progress(), "vault", {"where": where}, ["where"])
    return kit.engine()


@pytest.mark.parametrize("hidden", ["folder", "existing"])
def test_hidden_choice_is_not_offered_or_taken(state, hidden):
    eng = Kit(state).engine()
    screen = eng.submit("vault", {})
    assert [o["value"] for o in screen["field"]["options"]] == ["here"]
    overview = json.dumps(eng.overview())
    assert "A drive or folder I choose" not in overview and "A vault I already have" not in overview
    sent = eng.submit("vault", {"where": hidden, "folder": "D:\\Vault"})
    assert sent["status"] == "ask" and sent["field"]["id"] == "where" and sent["error"]
    assert [o["value"] for o in sent["field"]["options"]] == ["here"]
    assert "where" not in eng.answers_of("vault")


@pytest.mark.parametrize("status", ["open", "locked"])
def test_a_bot_with_a_vault_still_gets_back_in(state, status):
    """A second vault is refused, so hiding this from her too would loop at the PIN."""
    kit = Kit(state)
    kit.vault._status = status
    eng = kit.engine()
    assert [o["value"] for o in eng.submit("vault", {})["field"]["options"]] == ["here", "existing"]
    assert eng.submit("vault", {"where": "folder"})["field"]["id"] == "where"
    assert eng.submit("vault", {"where": "existing"})["field"]["label"] == "Which folder?"


def test_hidden_choice_is_not_in_the_terminal(state):
    run = TerminalRun(Kit(state))
    assert run.step("vault", vault_inputs())["status"] == "done"
    assert "A drive or folder I choose" not in run.all_text() and "A vault I already have" not in run.all_text()


def test_saved_folder_answer_keeps_working(state):
    kit = Kit(state)
    eng = saved_answer(kit, "folder")
    screen = eng.submit("vault", {})
    assert screen["field"]["id"] == "folder" and screen["answered"][0]["shown"] == "A drive or folder I choose"
    eng.submit("vault", {"folder": str(state / "kept"), "pin": "482913", "pin_again": "482913"})
    assert kit.vault.created == {"where": "folder", "folder": str(state / "kept")}


def test_saved_existing_answer_keeps_working(state):
    kit = Kit(state)
    eng = saved_answer(kit, "existing")
    screen = eng.submit("vault", {})
    assert screen["field"]["id"] == "folder" and screen["answered"][0]["shown"] == "A vault I already have"
    assert eng.submit("vault", {"folder": str(state), "pin": "482913"})["field"]["id"] == "data_folder"
    assert kit.vault.status() == "open"


@pytest.mark.parametrize("status, offered, pick, asked",
                         [("missing", ["here"], "here", "Pick a PIN for your vault, at least 8 characters"),
                          ("locked", ["here", "existing"], "existing", "Which folder?")])
def test_refused_folder_answer_asks_where_again(state, status, offered, pick, asked):
    """The real refusal (wiring.py) on a saved answer: she picks again, with nothing skipped after it."""
    from puffpai_core.wiring import WizardVault
    kit = Kit(state)
    kit.vault._status = status
    kit.vault.create = WizardVault(state).create
    eng = saved_answer(kit, "folder")
    screen = eng.submit("vault", {"folder": str(state / "kept"), "pin": "482913", "pin_again": "482913"})
    assert screen["field"]["id"] == "where" and "not built yet" in screen["error"]
    assert [o["value"] for o in screen["field"]["options"]] == offered
    assert eng.submit("vault", {"where": pick})["field"]["label"] == asked


def test_refused_existing_answer_asks_where_again(state):
    """The real refusal on a bot with no vault: not stuck at the PIN, and "On this computer" goes on."""
    from puffpai_core.wiring import WizardVault
    kit = Kit(state)
    kit.vault = WizardVault(state)
    eng = saved_answer(kit, "existing")
    screen = eng.submit("vault", {"folder": str(state / "another-bot"), "pin": "48291375"})
    assert screen["field"]["id"] == "where" and "not built yet" in screen["error"]
    assert [o["value"] for o in screen["field"]["options"]] == ["here"]
    assert eng.submit("vault", {"where": "here"})["field"]["label"] == "Pick a PIN for your vault, at least 8 characters"


def test_lost_code_gets_a_new_one(state):
    kit = Kit(state)
    eng = kit.engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    resumed = kit.engine().submit("vault", {})
    assert resumed["field"]["id"] == "recovery_confirm"
    assert any("type new" in line for line in resumed["lines"])
    asked = eng.submit("vault", {"recovery_confirm": "new"})
    assert asked["status"] == "ask" and asked["field"]["label"] == "Type your vault's PIN" and asked["field"]["hidden"]
    shown = eng.submit("vault", {"recovery_confirm": "482913"})
    assert shown["status"] == "show" and shown["show"]["value"] == "NEWC-0DE0-0000-0000"
    assert eng.submit("vault", {"recovery_confirm": "NEWC-0DE0-0000-0000"})["clear"] is True


def test_bad_pin_asks_again(state):
    eng = Kit(state).engine()
    screen = eng.submit("vault", {"where": "here", "pin": "12"})
    assert screen["status"] == "ask" and screen["field"]["id"] == "pin" and "6 digits" in screen["error"]


def test_no_cloud_option_without_providers(state):
    kit = Kit(state, providers=False)
    screen = kit.engine().submit("toolsmith", {})
    assert [o["value"] for o in screen["field"]["options"]] == ["local"]
    assert "No cloud model for Toolsmith is installed, so Toolsmith uses this computer." in screen["lines"]


def test_consent_no_is_not_a_yes(state):
    kit = Kit(state, vault="open")
    eng = kit.engine()
    screen = eng.submit("toolsmith", {"where": "cloud", "provider": "anthropic", "consent": "no"})
    assert screen["field"]["id"] == "consent" and "only after you agree" in screen["error"]


def test_whole_wizard_in_the_terminal(state):
    kit = Kit(state)
    run = TerminalRun(kit)
    answers = {**vault_inputs(), "model": "", "posture": "search", "where": ["here", "cloud"], "provider": "anthropic",
               "consent": "yes", "key": KEY, "goal": "a world news map", "yes": "yes"}
    run.inputs = {k: (list(v) if isinstance(v, list) else [v]) for k, v in answers.items()}
    code = run.term.run()                                # one terminal session, every step in order
    assert code == 0, run.all_text()[-2000:]
    prog = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))
    assert prog["complete"] is True and all(v["state"] == "done" for v in prog["steps"].values())
    assert kit.bridge.posture == "search" and kit.toolsmith.started
    assert run.all_text().count("World news map: works.") == 2       # Prove, then the Done summary
    assert KEY not in run.all_text() and "482913" not in run.all_text()


def test_toolsmith_local_is_not_done_before_the_brain(state):
    """Toolsmith "this computer" can't say done while the brain step is not set up (L5 verifier)."""
    from dataclasses import replace
    kit = Kit(state, providers=False)
    no_models = kit.engine()
    no_models.services = replace(kit.services(), models=None)
    screen = no_models.submit("toolsmith", {"where": "local"})
    assert screen["status"] == "not_built" and screen["lane"] == "L2"
    assert any("Your choice is saved" in line for line in screen["lines"])
    assert json.loads((state / "settings" / "toolsmith.json").read_text(encoding="utf-8")) == {"where": "local"}
    eng = kit.engine()
    screen = eng.submit("toolsmith", {"where": "local"})
    assert screen["status"] == "waiting" and "brain step" in screen["lines"][-1]
    assert eng.submit("brain", {"model": ""})["status"] == "done"
    screen = eng.submit("toolsmith", {"where": "local"})
    assert screen["status"] == "done" and screen["lines"][-1] == "Toolsmith uses the brain on this computer."


def test_bridge_not_built_does_not_claim_it_is_used(state):
    from dataclasses import replace
    kit = Kit(state)
    eng = kit.engine()
    eng.services = replace(kit.services(), bridge=None)
    screen = eng.submit("bridge", {"posture": "search"})
    assert screen["status"] == "not_built" and screen["lane"] == "L4"
    assert "nothing uses it until the Bridge is built" in screen["lines"][-1]
    assert "kept and used" not in " ".join(screen["lines"])
    assert not (state / "settings" / "bridge.json").exists()          # the Desktop never shows a posture that isn't held
