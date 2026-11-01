"""Acceptance tests T-PB001 to T-PB005 (03 1.6, D36), each run against BOTH renderers.

A case that passes in one renderer and fails in the other fails the suite.
Doubles stand in for the vault (L3), the model slot (L2), the Bridge (L4)
and Toolsmith (L6); see ``wizard_kit.py``.
"""

import json
import os

import pytest

from puffpai_core.wizard import StepListError, check_order, load_steps
from puffpai_core.wizard.terminal import CLEAR
from wizard_kit import CODE, KEY, RENDERERS, FakeModels, Kit, vault_inputs

TOOLSMITH_CLOUD = {"where": "cloud", "provider": "anthropic", "consent": "yes", "key": KEY, "cost_cap": ""}


@pytest.fixture(params=RENDERERS, ids=lambda r: r.name)
def renderer(request):
    return request.param


def _settings_text(state):
    return "\n".join(p.read_text(encoding="utf-8") for p in (state / "settings").glob("*.json"))


# -- T-PB001: the key is kept -----------------------------------------------------
@pytest.mark.parametrize("locked", [False, True], ids=["vault-open", "vault-locked"])
def test_pb001_key_kept(state, renderer, locked):
    kit = Kit(state)
    run = renderer(kit)
    assert run.step("vault", vault_inputs())["status"] == "done"
    if locked:
        kit.vault.lock()
    done = run.step("toolsmith", TOOLSMITH_CLOUD)
    assert done["status"] == "done"
    run.step("goal", {"goal": "a world news map"})
    run.step("plan", {"project": ""})
    assert run.step("yes", {"yes": "yes"})["status"] == "done"
    if locked:
        assert kit.toolsmith.key_at_yes == "inbox"
        assert "sealed in your vault" in run.all_text()
        kit.vault.unlock()
    else:
        assert kit.toolsmith.key_at_yes == "saved"
    assert kit.vault.key_status("toolsmith.anthropic") == "saved"
    assert kit.vault.secrets["toolsmith.anthropic"] == KEY
    assert KEY not in run.all_text()                       # never echoed
    assert KEY not in _settings_text(state)                # never in progress or settings
    record = json.loads((state / "settings" / "toolsmith.json").read_text(encoding="utf-8"))
    assert record["where"] == "cloud" and record["provider"] == "anthropic" and record["cost_cap_usd"] == 5
    assert len(record["consent"]["sha"]) == 64
    assert "key" in run.hidden_reads


def test_pb001_key_before_vault_is_refused_at_load(state):
    steps, _, _ = load_steps(state)
    ids = [s["id"] for s in steps]
    moved = [steps[ids.index("toolsmith")]] + [s for s in steps if s["id"] != "toolsmith"]
    with pytest.raises(StepListError, match="Step toolsmith asks for a key before the vault exists. "
                                            "Move it after the vault step."):
        check_order(moved)
    check_order(steps)                                     # the default order is fine


def test_pb001_no_vault_no_key_question(state, renderer):
    """A secret is never asked where it can't be kept: the choice is kept and the build holds."""
    kit = Kit(state)                                       # vault never set up (status missing)
    run = renderer(kit)
    done = run.step("toolsmith", TOOLSMITH_CLOUD)
    assert done["status"] == "done"
    assert "key" not in run.hidden_reads
    assert "Paste your key" not in run.all_text()
    assert "Toolsmith will wait for your key before its first run" in run.all_text()


# -- T-PB002: a hidden field is never asked -----------------------------------------
def test_pb002_hidden_field(state, renderer):
    kit = Kit(state)
    run = renderer(kit)
    assert run.step("vault", vault_inputs("here"))["status"] == "done"
    assert "Which folder?" not in run.all_text()


@pytest.mark.parametrize("renderer_name", ["terminal", "browser"])
def test_pb002_folder_sent_anyway_says_not_used(state, renderer_name):
    kit = Kit(state)
    eng = kit.engine()
    screen = eng.submit("vault", {"where": "here", "folder": "D:\\Vault"}, renderer=renderer_name)
    assert "You picked On this computer, so the folder you typed (D:\\Vault) was not used." in screen["lines"]
    assert screen["field"]["id"] == "pin"


def test_pb002_linux_examples_and_tilde(state, renderer):
    kit = Kit(state, platform="linux", env={"HOME": "/home/you"})
    kit.vault._status = "locked"                         # "Which folder?" is for a bot that has a vault (D111)
    run = renderer(kit)
    run.step("vault", {"where": "existing", "folder": "~/puffpai-vault", "pin": "482913",
                       "data_folder": "/tmp/pp-data-test", "linked": ""})
    text = run.all_text()
    assert "/media/you/MyDrive/PuffPAI Vault" in text
    assert "E:\\\\PuffPAI Vault" not in text and "E:\\PuffPAI Vault" not in text
    assert "Using /home/you/puffpai-vault" in text


def test_pb002_unknown_variable_is_plain(state):
    kit = Kit(state)
    kit.vault._status = "locked"
    eng = kit.engine()
    eng.submit("vault", {"where": "existing"})
    screen = eng.submit("vault", {"folder": "%NOPE_NOT_SET%\\Vault"})
    assert screen["status"] == "ask" and screen["field"]["id"] == "folder"
    assert "There is no variable named NOPE_NOT_SET" in screen["error"]


# -- T-PB003: no echo, honest clearing ----------------------------------------------
@pytest.mark.parametrize("tmux", [False, True], ids=["plain", "tmux"])
def test_pb003_terminal_no_echo_and_clear(state, tmux):
    from wizard_kit import TerminalRun
    env = {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%3"} if tmux else {}
    run = TerminalRun(Kit(state), env=env)
    run.step("vault", vault_inputs())
    log = run.all_text()
    assert log.count(CODE) == 1                         # shown once, the typed-back copy never echoed
    assert "recovery_confirm" in run.hidden_reads and "pin" in run.hidden_reads
    assert "482913" not in log
    assert log.index(CODE) < log.index(CLEAR)
    assert CODE not in run.screen_text()
    assert "\x1b[3J\x1b[2J\x1b[H" in log
    assert "Your terminal or SSH app may still have the code in its scroll history" in run.screen_text()
    assert "gone" not in log.lower()
    assert (["tmux", "clear-history", "-t", "%3"] in run.ran) is tmux


def test_pb003_classic_console_writes_nothing_and_claims_nothing(state):
    """A classic Windows console that can't read escape codes: nothing is written, nothing is claimed."""
    from wizard_kit import TerminalRun
    from puffpai_core.wizard import lines as L
    run = TerminalRun(Kit(state), vt=False)
    run.step("vault", vault_inputs())
    log = run.all_text()
    assert "\x1b" not in log                              # no ESC[3J ESC[2J ESC[H printed as junk
    assert L.NOT_CLEARED in log
    assert "cleared this screen" not in log.lower() and "buildbot cleared" not in log.lower()
    assert "Your terminal may still have it in its scroll history" in log    # said with the code itself


def test_pb003_no_renderer_claims_a_clear(state, renderer):
    run = renderer(Kit(state))
    run.step("vault", vault_inputs())
    assert "cleared this screen" not in run.all_text().lower()


def test_pb003_clear_only_on_a_terminal_that_reads_the_codes():
    import io
    from puffpai_core.wizard.terminal import Terminal
    for tty, vt, wrote in ((True, True, True), (True, False, False), (False, True, False)):
        out = io.StringIO()
        t = Terminal(None, out=out, env={}, tty=lambda _o, v=tty: v, vt_on=lambda _o, v=vt: v)
        assert t.clear() is wrote and (CLEAR in out.getvalue()) is wrote
    broken = Terminal(None, out=io.StringIO(), env={}, tty=lambda _o: True,
                      vt_on=lambda _o: (_ for _ in ()).throw(OSError("no console")))
    assert broken.clear() is False                        # it never raises


def test_pb003_windows_vt_on_without_a_console():
    import io
    from puffpai_core.wizard.terminal import windows_vt_on
    assert windows_vt_on(io.StringIO()) is (os.name != "nt")   # no console handle: False on Windows


def test_pb003_browser_removes_the_code(state):
    from wizard_kit import BrowserRun
    run = BrowserRun(Kit(state))
    run.step("vault", vault_inputs())
    seen = run.visible()
    assert CODE not in seen                              # removed from the page after the match
    assert "482913" not in " ".join(seen)
    assert "BuildBot removed the code from this page. A screenshot or a printed copy is yours to keep safe." in seen
    assert any(CODE == s["show"]["value"] for s in run.screens if s.get("show"))   # it was shown once
    assert "gone" not in run.all_text().lower()


def test_pb003_wrong_code_then_right(state, renderer):
    kit = Kit(state)
    run = renderer(kit)
    inputs = vault_inputs()
    inputs["recovery_confirm"] = ["WRONG-CODE", CODE]
    assert run.step("vault", inputs)["status"] == "done"
    assert "That does not match the code shown" in run.all_text()
    assert "WRONG-CODE" not in run.all_text()


# -- T-PB004: named brain states -----------------------------------------------------
LINES = {
    "not_downloaded": "Your bot's brain is still downloading (4 of 19 GB)",
    "loading": "Your bot's brain is still starting up",
    "timed_out": "Your bot's brain did not answer in time",
    "error": "Your bot's brain could not answer (the model server is not running)",
}


@pytest.mark.parametrize("brain", list(LINES))
def test_pb004_brain_state(state, renderer, brain):
    kit = Kit(state)
    kit.models.current = {"state": brain, "done_gb": 4, "size_gb": 19, "reason": "the model server is not running"}
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "model.json").write_text('{"name": "qwen3:30b-a3b"}', encoding="utf-8")
    run = renderer(kit)
    run.step("goal", {"goal": "a world news map"})
    text = run.all_text()
    assert LINES[brain] in text
    assert ("did not answer in time" in text) is (brain == "timed_out")
    assert kit.toolsmith.asked == [("a world news map", False)]      # the brain is not asked unless ready


def test_pb004_ready_uses_the_brain(state, renderer):
    kit = Kit(state)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "model.json").write_text('{"name": "qwen3:30b-a3b"}', encoding="utf-8")
    run = renderer(kit)
    done = run.step("goal", {"goal": "a world news map"})
    assert done["status"] == "done"
    assert kit.toolsmith.asked == [("a world news map", True)]
    assert "Job 1: World news map" in run.all_text()


# -- T-PB005: the benchmark pick ----------------------------------------------------
def _jetson(free_gb, board=True):
    return {"schema": "hardware_census/v1", "os": {"system": "Linux"}, "memory": {"total_gb": 61.4},
            "hardware_class": "workstation",
            "soc": {"model": "NVIDIA Jetson AGX Orin Developer Kit" if board else None, "is_jetson": board},
            "accelerator": {"kind": "jetson_integrated" if board else "unknown"}, "cpu": {},
            "disk": {"total_gb": 900, "free_gb": free_gb}, "notes": []}


@pytest.mark.parametrize("case, free, board, want", [
    ("1", 857, True, "qwen3:30b-a3b"), ("2", 20, True, "qwen3.5:9b"), ("3", 857, False, "qwen3.5:9b")])
def test_pb005_benchmark_pick(state, renderer, case, free, board, want):
    kit = Kit(state, platform="linux")
    kit.census = _jetson(free, board)
    run = renderer(kit)
    run.step("scan")
    run.step("brain", {"model": ""})                       # Enter: the Recommended pick
    picked = json.loads((state / "settings" / "model.json").read_text(encoding="utf-8"))["name"]
    assert picked == want
    text = run.all_text()
    assert "Recommended" in text
    if case == "1":
        assert "19 GB download" in text and "Jetson AGX Orin 64GB" in text
    else:
        assert "Jetson AGX Orin 64GB" not in text          # no benchmark claims without the rule
    assert kit.models.pulled == [want]


def test_pb005_real_menu_when_lane_l2_is_in(state, renderer):
    """The real recommender (lane L2's zone2.models.fit, 03 1.5), through the wizard's brain step."""
    from puffpai_core.zone2 import models
    pick = lambda census: models.fit(census, [])["recommended"]["id"]  # noqa: E731
    fit = models.fit(_jetson(857), [])
    best = next(m for m in fit["options"] if m.get("recommended"))
    assert best["id"] == fit["recommended"]["id"] == "qwen3:30b-a3b" and "19 GB download" in best.get("reason", "")
    assert models.menu(_jetson(857), []) == fit["options"]
    assert pick(_jetson(20)) != "qwen3:30b-a3b"
    assert pick(_jetson(857, board=False)) != "qwen3:30b-a3b"

    class RealFit(FakeModels):                             # the real fit; nothing installed, no Ollama call
        def fit(self, census):
            return models.fit(census, [])

    kit = Kit(state, platform="linux")
    kit.census = _jetson(857)
    kit.models = RealFit()
    run = renderer(kit)
    run.step("scan")
    run.step("brain", {"model": ""})                       # Enter: the Recommended pick
    assert json.loads((state / "settings" / "model.json").read_text(encoding="utf-8"))["name"] == "qwen3:30b-a3b"
    assert "19 GB download" in run.all_text() and kit.models.pulled == ["qwen3:30b-a3b"]
