"""Lane i23f (D106): typing new at the recovery code question really makes a new code.

Every test here runs the wizard engine on the real vault (``wiring.WizardVault``, real Argon2id wraps, a keyring on
disk in the test's own folder), through the same call both renderers use. The codes are random to each test and the
PIN is made up. The step comes before the goal, so no goal changes what it does: the one full run uses a birdwatching
bot only because setup needs some goal.
"""

import io
import json
import logging
import re
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import wiring
from puffpai_core.bus import Bus
from puffpai_core.wizard import Terminal
from puffpai_core.wizard import lines as L
from puffpai_core.wizard import terminal as term_mod
from puffpai_core.wizard.terminal import CLEAR
from puffpai_core.zone1 import archivist as arch_mod
from puffpai_core.zone1 import vault as vault_mod
from puffpai_core.zone1.pin_rules import generate_recovery_code, normalize_recovery_code, validate_vault_secret
from puffpai_core.zone1.vault import SafeMode, WrongSecret
from wizard_kit import CENSUS, DESKTOP, BrowserRun, Kit

PIN = "Tq7#mz2Lp!"                     # a made-up test PIN (z1kit's)
OTHER_PIN = "Zk4!wr9Bn#"               # made up too: a well-formed PIN that is not this vault's
CODE_SHAPE = re.compile(r"[A-Z0-9]{5}(?:-[A-Z0-9]{5}){4}")


class Recorder:
    """A bus that keeps every event, to look for values in them."""
    message = staticmethod(Bus.message)

    def __init__(self):
        self.events = []

    def publish(self, msg):
        self.events.append(msg)


def begin(state, bus=None):
    """A bot in the vault step, at the typed-back question: kit, engine, the first code."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state, bus)
    eng = kit.engine(bus=bus) if bus is not None else kit.engine()
    screen = eng.submit("vault", {"where": "here", "pin": PIN, "pin_again": PIN})
    assert screen["status"] == "show" and screen["field"]["id"] == "recovery_confirm"
    return kit, eng, screen["show"]["value"]


def new_code(eng, word="new", pin=PIN):
    asked = eng.submit("vault", {"recovery_confirm": word})
    assert asked["status"] == "ask" and asked["field"]["id"] == "recovery_confirm"
    assert asked["field"]["label"] == "Type your vault's PIN" and asked["field"]["hidden"] is True
    assert asked["error"] in (L.NEW_CODE_ASK_PIN, L.NEW_CODE_ASK_PIN_LOCKED)
    shown = eng.submit("vault", {"recovery_confirm": pin})
    assert shown["status"] == "show" and shown["field"]["id"] == "recovery_confirm"
    assert L.OLD_CODE_GONE in shown["show"]["line"]
    return shown["show"]["value"]


def vault_of(state):
    return arch_mod.for_state(state).vault


def restart(state):
    """What a closed window leaves: nothing in memory, so the vault is locked."""
    vault_of(state).lock("test")
    arch_mod._BY_STATE.pop(str(Path(state).resolve()), None)


def opens_with(state, method, secret):
    v = vault_of(state)
    v.lock("test")
    try:
        v.unlock(method, secret)
    except WrongSecret:
        return False
    return v.is_open()


def no_rotate(monkeypatch):
    """Any call of the vault's rotate fails the test."""
    def boom(self, pin):
        raise AssertionError("the vault was asked for a new code")
    monkeypatch.setattr(vault_mod.Vault, "rotate_recovery", boom)


def files_holding(state, secrets):
    """Every file under the bot folder holding one of *secrets*, as typed or normalized."""
    wanted = {s.encode() for s in secrets} | {normalize_recovery_code(s).encode() for s in secrets}
    return [str(p) for p in Path(state).rglob("*") if p.is_file() and any(w in p.read_bytes() for w in wanted)]


# -- the new code --------------------------------------------------------------------------------------------------
def test_new_gives_a_new_code_old_is_dead_pin_still_opens(state):
    _, eng, first = begin(state)
    second = new_code(eng)
    assert CODE_SHAPE.fullmatch(second) and second != first
    v = vault_of(state)
    assert v.test_recovery(first) is False and v.test_recovery(second) is True
    assert opens_with(state, "recovery", first) is False
    assert opens_with(state, "recovery", second) is True
    assert opens_with(state, "pin", PIN) is True


def test_the_step_tests_the_newest_code_and_refuses_the_old(state):
    _, eng, first = begin(state)
    second = new_code(eng)
    again = eng.submit("vault", {"recovery_confirm": first})
    assert again["status"] == "ask" and again["error"] == L.CODE_MISMATCH and "show" not in again
    done = eng.submit("vault", {"recovery_confirm": second})
    assert done["clear"] is True and done["field"]["id"] == "data_folder"


def test_new_three_times_leaves_only_the_last(state):
    _, eng, first = begin(state)
    codes = [first, new_code(eng), new_code(eng), new_code(eng)]
    assert len(set(codes)) == 4
    v = vault_of(state)
    assert [v.test_recovery(c) for c in codes] == [False, False, False, True]
    assert opens_with(state, "recovery", codes[2]) is False
    assert opens_with(state, "recovery", codes[3]) is True


@pytest.mark.parametrize("word", ["NEW", " NEW ", "New", "\tnew\n"])
def test_case_and_spaces_do_not_matter(state, word):
    _, eng, first = begin(state)
    assert new_code(eng, word) != first


def test_a_code_or_a_pin_can_never_be_the_word_new():
    assert len(normalize_recovery_code(generate_recovery_code())) == 25
    assert validate_vault_secret("new")[0] is False


def test_a_wrong_code_asks_again_and_does_not_show_it(state):
    _, eng, first = begin(state)
    screen = eng.submit("vault", {"recovery_confirm": "WRONG-CODE"})
    assert screen["status"] == "ask" and screen["error"] == L.CODE_MISMATCH
    assert "WRONG-CODE" not in json.dumps(screen)
    assert eng.submit("vault", {"recovery_confirm": first})["clear"] is True


def test_too_many_wrong_codes_ask_in_plain_words_and_new_still_works(state):
    """The vault's wait reaches her as words: before lane i23f it stopped the step with an error."""
    _, eng, _ = begin(state)
    for _ in range(vault_mod.FREE_ATTEMPTS + 1):
        eng.submit("vault", {"recovery_confirm": "WRONG-CODE"})
    screen = eng.submit("vault", {"recovery_confirm": "WRONG-CODE"})
    assert screen["status"] == "ask" and "Too many tries" in screen["error"]
    second = new_code(eng)
    v = vault_of(state)
    v._attempts.pop("recovery_test", None)       # the wait is over
    assert eng.submit("vault", {"recovery_confirm": second})["clear"] is True


# -- the PIN --------------------------------------------------------------------------------------------------------
def test_a_wrong_pin_makes_no_new_code_and_goes_back_to_the_code_question(state, monkeypatch):
    _, eng, first = begin(state)
    assert eng.submit("vault", {"recovery_confirm": "new"})["field"]["label"] == "Type your vault's PIN"
    screen = eng.submit("vault", {"recovery_confirm": OTHER_PIN})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_WRONG_PIN and "show" not in screen
    assert screen["field"]["label"] == "Type the recovery code back"
    assert OTHER_PIN not in json.dumps(screen)
    assert vault_of(state).test_recovery(first) is True
    assert new_code(eng) != first                # she can try again


def test_too_many_wrong_pins_wait_in_plain_words(state):
    _, eng, first = begin(state)
    for _ in range(vault_mod.FREE_ATTEMPTS + 1):
        eng.submit("vault", {"recovery_confirm": "new"})
        eng.submit("vault", {"recovery_confirm": OTHER_PIN})
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = eng.submit("vault", {"recovery_confirm": PIN})
    assert screen["status"] == "ask" and "Too many tries" in screen["error"] and "show" not in screen
    assert vault_of(state).test_recovery(first) is True


def test_new_again_at_the_pin_question_costs_no_pin_try(state):
    _, eng, _ = begin(state)
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = eng.submit("vault", {"recovery_confirm": "new"})
    assert screen["error"] == L.NEW_CODE_ASK_PIN and screen["field"]["label"] == "Type your vault's PIN"
    assert "pin" not in vault_of(state)._attempts


def test_the_pin_question_does_not_say_type_new(state):
    _, eng, _ = begin(state)
    assert any("type new" in line for line in eng.submit("vault", {})["lines"])
    eng.submit("vault", {"recovery_confirm": "new"})
    assert L.CODE_LOST not in eng.submit("vault", {})["lines"]


# -- when it is refused ---------------------------------------------------------------------------------------------
def test_an_existing_vault_opened_in_setup_gets_no_new_code(state, monkeypatch):
    code = arch_mod.for_state(state).setup(PIN)
    restart(state)
    no_rotate(monkeypatch)
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    eng = kit.engine()
    screen = eng.submit("vault", {"where": "existing", "folder": str(state), "pin": PIN, "recovery_confirm": "new"})
    assert "show" not in screen and screen["field"]["id"] == "data_folder"
    assert any("was not used" in line for line in screen["lines"])
    assert vault_of(state).test_recovery(code) is True


def test_the_pin_opens_a_vault_that_locked_itself_and_makes_the_new_code(state):
    _, eng, first = begin(state)
    v = vault_of(state)
    v.lock("idle")
    asked = eng.submit("vault", {"recovery_confirm": "new"})
    assert asked["status"] == "ask" and asked["error"] == L.NEW_CODE_ASK_PIN_LOCKED
    assert asked["field"]["label"] == "Type your vault's PIN" and asked["field"]["hidden"] is True
    assert v.is_open() is False                          # "new" alone opens nothing
    shown = eng.submit("vault", {"recovery_confirm": PIN})
    assert shown["status"] == "show" and L.OLD_CODE_GONE in shown["show"]["line"]
    second = shown["show"]["value"]
    assert v.is_open() is True
    assert v.test_recovery(first) is False and v.test_recovery(second) is True
    assert eng.submit("vault", {"recovery_confirm": second})["clear"] is True
    assert opens_with(state, "recovery", first) is False
    assert opens_with(state, "recovery", second) is True
    assert opens_with(state, "pin", PIN) is True


def test_a_vault_that_locks_at_the_pin_question_opens_with_that_pin(state):
    _, eng, first = begin(state)
    eng.submit("vault", {"recovery_confirm": "new"})
    vault_of(state).lock("idle")
    shown = eng.submit("vault", {"recovery_confirm": PIN})
    assert shown["status"] == "show" and vault_of(state).is_open() is True
    assert vault_of(state).test_recovery(first) is False


def test_a_wrong_pin_leaves_a_locked_vault_locked_and_counts_as_a_try(state, monkeypatch):
    _, eng, first = begin(state)
    v = vault_of(state)
    v.lock("idle")
    no_rotate(monkeypatch)
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = eng.submit("vault", {"recovery_confirm": OTHER_PIN})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_WRONG_PIN and "show" not in screen
    assert screen["field"]["label"] == "Type the recovery code back"
    assert v.is_open() is False and v._attempts["pin"][0] == 1
    assert v.test_recovery(first) is True


def test_the_vault_throttle_holds_on_a_locked_vault(state, monkeypatch):
    _, eng, first = begin(state)
    v = vault_of(state)
    v.lock("idle")
    no_rotate(monkeypatch)
    for _ in range(vault_mod.FREE_ATTEMPTS + 1):
        eng.submit("vault", {"recovery_confirm": "new"})
        eng.submit("vault", {"recovery_confirm": OTHER_PIN})
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = eng.submit("vault", {"recovery_confirm": PIN})      # the right PIN waits too
    assert "Too many tries" in screen["error"] and "show" not in screen
    assert v.is_open() is False and v.test_recovery(first) is True


def test_a_locked_vault_this_setup_is_not_finishing_is_refused(state, monkeypatch):
    """No sign in progress that this setup made it and is having the code typed back: the PIN opens nothing here."""
    _, _, first = begin(state)
    restart(state)
    wizard_json = Path(state) / "settings" / "wizard.json"
    prog = json.loads(wizard_json.read_text(encoding="utf-8"))
    prog["partial"]["vault"]["answers"].pop("_shown_before")
    wizard_json.write_text(json.dumps(prog), encoding="utf-8")
    no_rotate(monkeypatch)
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    screen = kit.engine().submit("vault", {"recovery_confirm": "new"})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_LOCKED
    assert screen["field"]["label"] == "Type the recovery code back"
    assert vault_of(state).is_open() is False and vault_of(state).test_recovery(first) is True


def test_a_vault_this_setup_is_not_finishing_that_locks_at_the_pin_question_stays_locked(state, monkeypatch):
    kit, _, first = begin(state)
    wizard_json = Path(state) / "settings" / "wizard.json"
    prog = json.loads(wizard_json.read_text(encoding="utf-8"))
    prog["partial"]["vault"]["answers"].pop("_shown_before")
    wizard_json.write_text(json.dumps(prog), encoding="utf-8")
    eng = kit.engine()
    assert eng.submit("vault", {"recovery_confirm": "new"})["error"] == L.NEW_CODE_ASK_PIN      # it is open: PIN only
    vault_of(state).lock("idle")
    no_rotate(monkeypatch)
    screen = eng.submit("vault", {"recovery_confirm": PIN})
    assert screen["error"] == L.NEW_CODE_LOCKED and "show" not in screen
    assert vault_of(state).is_open() is False and vault_of(state).test_recovery(first) is True


def test_the_service_opens_a_locked_vault_only_when_told(state):
    _, _, first = begin(state)
    v = vault_of(state)
    v.lock("idle")
    with pytest.raises(ValueError) as refused:
        wiring.WizardVault(state).rotate_recovery(PIN)
    assert str(refused.value) == L.NEW_CODE_LOCKED and v.is_open() is False
    assert v.test_recovery(first) is True


def test_an_existing_locked_vault_opens_nothing_from_the_word_new(state, monkeypatch):
    code = arch_mod.for_state(state).setup(PIN)
    restart(state)
    no_rotate(monkeypatch)
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    eng = kit.engine()
    screen = eng.submit("vault", {"where": "existing", "folder": str(state), "recovery_confirm": "new"})
    assert screen["field"]["id"] == "pin" and "show" not in screen
    assert vault_of(state).is_open() is False
    wrong = eng.submit("vault", {"pin": OTHER_PIN})
    assert wrong["field"]["id"] == "pin" and vault_of(state).is_open() is False
    assert vault_of(state).test_recovery(code) is True


def test_safe_mode_is_not_repaired_from_this_step(state, monkeypatch):
    """The vault's rotate also repairs safe mode; this step never does that, even with the right PIN."""
    _, eng, first = begin(state)
    v = vault_of(state)
    ring = json.loads(v.keyring_path.read_text(encoding="utf-8"))
    ring["mac"] = "0" * len(ring["mac"])
    v.keyring_path.write_text(json.dumps(ring), encoding="utf-8")
    v.lock("test")
    with pytest.raises(SafeMode):
        v.unlock("pin", PIN)
    no_rotate(monkeypatch)
    screen = eng.submit("vault", {"recovery_confirm": "new"})
    assert screen["error"] == L.NEW_CODE_SAFE_MODE and "Vault on the Desktop" in screen["error"]
    assert screen["field"]["label"] == "Type the recovery code back" and v.is_open() is False
    assert v.test_recovery(first) is True


def test_safe_mode_found_by_the_pin_points_to_the_repair_and_does_not_repeat(state, monkeypatch):
    """The keyring's MAC needs the vault key, so here the PIN is what finds it; after that the word says so at once."""
    _, eng, first = begin(state)
    v = vault_of(state)
    ring = json.loads(v.keyring_path.read_text(encoding="utf-8"))
    ring["mac"] = "0" * len(ring["mac"])
    v.keyring_path.write_text(json.dumps(ring), encoding="utf-8")
    v.lock("test")
    no_rotate(monkeypatch)
    assert eng.submit("vault", {"recovery_confirm": "new"})["error"] == L.NEW_CODE_ASK_PIN_LOCKED
    found = eng.submit("vault", {"recovery_confirm": PIN})
    assert found["error"] == L.NEW_CODE_SAFE_MODE and "show" not in found and v.is_open() is False
    again = eng.submit("vault", {"recovery_confirm": "new"})
    assert again["error"] == L.NEW_CODE_SAFE_MODE and again["field"]["label"] == "Type the recovery code back"
    assert v.test_recovery(first) is True


def test_a_deleted_keyring_says_so_not_locked(state, monkeypatch):
    _, eng, _ = begin(state)
    v = vault_of(state)
    v.lock("idle")
    v.keyring_path.unlink()
    no_rotate(monkeypatch)
    screen = eng.submit("vault", {"recovery_confirm": "new"})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_NO_VAULT
    assert "locked" not in screen["error"] and screen["field"]["label"] == "Type the recovery code back"


@pytest.mark.parametrize("when", ["before the word", "at the PIN question"])
def test_an_unreadable_keyring_says_so_not_locked(state, monkeypatch, when):
    _, eng, _ = begin(state)
    v = vault_of(state)
    v.lock("idle")
    no_rotate(monkeypatch)
    if when == "at the PIN question":
        eng.submit("vault", {"recovery_confirm": "new"})
    v.keyring_path.write_text("{not json", encoding="utf-8")
    word = PIN if when == "at the PIN question" else "new"
    screen = eng.submit("vault", {"recovery_confirm": word})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_UNREADABLE
    assert screen["field"]["label"] == "Type the recovery code back"


def test_a_missing_vault_drive_says_so_not_locked(state, monkeypatch):
    _, eng, _ = begin(state)
    v = vault_of(state)
    v.lock("idle")
    monkeypatch.setattr(v.store, "health", lambda: {"ok": False, "reason": "missing"})
    no_rotate(monkeypatch)
    assert eng.submit("vault", {"recovery_confirm": "new"})["error"] == vault_mod.StoreMissing.__doc__


# -- a lone surrogate ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("locked", [False, True], ids=["open", "locked"])
def test_a_lone_surrogate_at_the_pin_question_is_wrong_input_no_raw_error(state, locked):
    _, eng, first = begin(state)
    v = vault_of(state)
    if locked:
        v.lock("idle")
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = eng.submit("vault", {"recovery_confirm": "\ud800"})
    assert screen["status"] == "ask" and screen["error"] == L.CANT_READ and "show" not in screen
    assert "codec" not in json.dumps(screen) and "pin" not in v._attempts
    assert v.is_open() is (not locked) and v.test_recovery(first) is True
    assert eng.submit("vault", {"recovery_confirm": PIN})["status"] == "show"


def test_a_lone_surrogate_as_the_pin_of_an_existing_vault_gets_no_raw_error(state):
    arch_mod.for_state(state).setup(PIN)
    restart(state)
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    screen = kit.engine().submit("vault", {"where": "existing", "folder": str(state), "pin": "\ud800"})
    assert screen["field"]["id"] == "pin" and screen["error"] == L.CANT_READ and vault_of(state).is_open() is False


# -- a finished bot: no wizard path opens a vault -------------------------------------------------------------------------
def finished_bot(state):
    """A bot through setup, vault open: the page's run, a program's key, the code."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)
    code = run.post("vault", {"where": "here", "pin": PIN, "pin_again": PIN})["show"]["value"]
    run.post("vault", {"recovery_confirm": code})
    assert run.post("vault", {"data_folder": "", "knowledge_folder": "", "linked": ""})["status"] == "done"
    path = Path(state) / "settings" / "wizard.json"
    prog = json.loads(path.read_text(encoding="utf-8"))
    prog["complete"] = True
    path.write_text(json.dumps(prog), encoding="utf-8")
    return run, run.app.extensions["puffpai"].tokens.issue_owner_key("a program"), code


def caller(run, key, who):
    """Post to the wizard as a program with the owner's API key, or as the owner at a paired device."""
    def post(step, answers):
        if who == "api key":
            client, headers = run.app.test_client(), {"Authorization": "Bearer " + key, "Host": "puffpai.localhost:8499"}
        else:
            client, headers = run.client, DESKTOP
        got = client.post(f"/v1/wizard/{step}", json={"answers": answers, "renderer": "browser"}, headers=headers)
        assert got.status_code == 200
        return got.json
    return post


WHO = ["api key", "paired device"]


@pytest.mark.parametrize("who", WHO)
@pytest.mark.parametrize("pin", [PIN, OTHER_PIN], ids=["right PIN", "wrong PIN"])
def test_a_finished_bot_never_opens_a_locked_vault_from_the_wizard(state, who, pin):
    run, key, code = finished_bot(state)
    v = vault_of(state)
    v.lock("owner")
    data_before = (Path(state) / "settings" / "data.json").read_bytes()
    post = caller(run, key, who)
    log_before = v.log_path.read_text(encoding="utf-8")
    screen = post("vault", {"where": "existing", "folder": str(state), "pin": pin,
                            "data_folder": str(Path(state) / "other"), "knowledge_folder": "", "linked": ""})
    assert screen["status"] == "ask" and screen["error"] == L.SETUP_DONE_VAULT and screen["field"]["id"] == "pin"
    assert v.is_open() is False and "pin" not in v._attempts       # refused the same for any PIN: no try, no answer
    assert v.log_path.read_text(encoding="utf-8") == log_before
    assert (Path(state) / "settings" / "data.json").read_bytes() == data_before
    assert v.test_recovery(code) is True


def test_her_api_key_still_cannot_unlock_by_the_vault_route(state):
    run, key, _ = finished_bot(state)
    vault_of(state).lock("owner")
    got = run.app.test_client().post("/v1/vault/unlock", json={"method": "pin", "secret": PIN},
                                     headers={"Authorization": "Bearer " + key, "Host": "puffpai.localhost:8499"})
    assert got.status_code == 403 and vault_of(state).is_open() is False


@pytest.mark.parametrize("who", WHO)
def test_a_finished_bot_can_still_run_the_vault_step_again(state, who):
    """Settings says "Run any step again to change it": with the vault open, the PIN is checked and nothing is opened."""
    run, key, _ = finished_bot(state)
    v = vault_of(state)
    session = v.session
    post = caller(run, key, who)
    wrong = post("vault", {"where": "existing", "folder": str(state), "pin": OTHER_PIN})
    assert wrong["field"]["id"] == "pin" and wrong["error"] == WrongSecret.__doc__ and v._attempts["pin"][0] == 1
    opened = post("vault", {"pin": PIN})
    assert opened["field"]["id"] == "data_folder" and "pin" not in v._attempts
    assert v.session == session                          # the same open: it was not opened again
    other = Path(state).parent / "elsewhere"
    assert post("vault", {"data_folder": str(other), "knowledge_folder": "", "linked": ""})["status"] == "done"
    assert json.loads((Path(state) / "settings" / "data.json").read_text(encoding="utf-8"))["data_folder"] == str(other)


@pytest.mark.parametrize("who", WHO)
def test_a_stale_flag_on_a_finished_bot_opens_nothing(state, who, monkeypatch):
    run, key, code = finished_bot(state)
    path = Path(state) / "settings" / "wizard.json"
    prog = json.loads(path.read_text(encoding="utf-8"))
    prog["partial"]["vault"] = {"answers": {"where": "here", "_shown_before": True},
                                "settled": ["where", "pin", "pin_again", "recovery"]}
    prog["steps"]["vault"] = {"state": "todo"}
    path.write_text(json.dumps(prog), encoding="utf-8")
    v = vault_of(state)
    v.lock("owner")
    no_rotate(monkeypatch)
    post = caller(run, key, who)
    screen = post("vault", {"recovery_confirm": "new"})
    assert screen["error"] == L.NEW_CODE_LOCKED and screen["field"]["label"] == "Type the recovery code back"
    assert v.is_open() is False and v.test_recovery(code) is True


@pytest.mark.parametrize("who", WHO)
def test_setup_that_is_not_finished_still_opens_an_existing_vault(state, who):
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)
    arch_mod.for_state(state).setup(PIN)
    vault_of(state).lock("owner")
    key = run.app.extensions["puffpai"].tokens.issue_owner_key("a program")
    screen = caller(run, key, who)("vault", {"where": "existing", "folder": str(state), "pin": PIN})
    assert screen["field"]["id"] == "data_folder" and vault_of(state).is_open() is True


def test_only_during_this_step(state, monkeypatch):
    """Before the vault exists, or after the step is done, the word reaches nothing."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    eng = kit.engine()
    with monkeypatch.context() as m:
        no_rotate(m)
        early = eng.submit("vault", {"recovery_confirm": "new"})
        assert early["field"]["id"] == "where" and not vault_of(state).is_set_up()
    code = eng.submit("vault", {"where": "here", "pin": PIN, "pin_again": PIN})["show"]["value"]
    eng.submit("vault", {"recovery_confirm": code})
    assert eng.submit("vault", {"data_folder": "", "knowledge_folder": "", "linked": ""})["status"] == "done"
    no_rotate(monkeypatch)
    late = eng.submit("vault", {"recovery_confirm": "new"})
    assert late["field"]["id"] == "where" and "show" not in late
    # Picking "here" again on the same vault is refused by the vault, so the question is never reached.
    again = eng.submit("vault", {"where": "here", "pin": PIN, "pin_again": PIN, "recovery_confirm": "new"})
    assert "show" not in again and again["field"]["id"] == "pin"
    assert vault_of(state).test_recovery(code) is True


# -- a failed write --------------------------------------------------------------------------------------------------
def test_a_failed_write_leaves_the_old_code_working(state, monkeypatch):
    _, eng, first = begin(state)
    v = vault_of(state)
    before = v.keyring_path.read_bytes()
    real = vault_mod.os.replace

    def fail_on_keyring(src, dst):
        if Path(dst).name == "keyring.json":
            raise OSError("disk full")
        return real(src, dst)

    eng.submit("vault", {"recovery_confirm": "new"})
    with monkeypatch.context() as m:
        m.setattr(vault_mod.os, "replace", fail_on_keyring)
        screen = eng.submit("vault", {"recovery_confirm": PIN})
    assert screen["status"] == "ask" and screen["error"] == L.NEW_CODE_NOT_SAVED and "show" not in screen
    assert "disk full" not in json.dumps(screen)
    assert v.keyring_path.read_bytes() == before
    assert v.test_recovery(first) is True
    assert "nothing changed" in screen["error"] and v.is_open() is True          # it was open before, and still is
    assert '"recovery_rotated"' not in v.log_path.read_text(encoding="utf-8")   # no line for what did not happen
    assert opens_with(state, "recovery", first) is True
    assert opens_with(state, "pin", PIN) is True
    assert new_code(eng) != first                        # and it works once the disk does


def test_a_failed_write_after_the_pin_opened_the_vault_says_it_is_open(state, monkeypatch):
    _, eng, first = begin(state)
    v = vault_of(state)
    v.lock("idle")
    real = vault_mod.os.replace

    def fail_on_keyring(src, dst):
        if Path(dst).name == "keyring.json":
            raise OSError("disk full")
        return real(src, dst)

    eng.submit("vault", {"recovery_confirm": "new"})
    with monkeypatch.context() as m:
        m.setattr(vault_mod.os, "replace", fail_on_keyring)
        screen = eng.submit("vault", {"recovery_confirm": PIN})
    assert screen["error"] == L.NEW_CODE_NOT_SAVED_OPENED and "show" not in screen
    assert screen["error"] == ("Your vault is open now, but BuildBot could not save a new code. "
                               "The code you were shown still works.")
    assert "nothing changed" not in screen["error"]
    assert v.is_open() is True and v.test_recovery(first) is True


# -- no trace of a value ----------------------------------------------------------------------------------------------
def test_no_code_or_pin_in_a_log_wizard_json_event_or_known(state, caplog):
    caplog.set_level(logging.DEBUG)
    bus = Recorder()
    _, eng, first = begin(state, bus)
    wizard_json = Path(state) / "settings" / "wizard.json"
    codes = [first]
    for round_ in range(3):
        if round_ == 2:
            vault_of(state).lock("idle")                # the PIN that opens a locked vault leaves no trace either
        codes.append(new_code(eng))
        saved = wizard_json.read_text(encoding="utf-8")
        assert not any(c in saved or normalize_recovery_code(c) in saved for c in codes) and PIN not in saved
    partial = json.loads(wizard_json.read_text(encoding="utf-8"))["partial"]["vault"]
    assert partial["answers"] == {"where": "here", "_shown_before": True}      # a flag, no value

    reopened = eng.submit("vault", {})                  # the screen after the show carries no code
    assert not any(c in json.dumps(reopened) for c in codes)

    done = eng.submit("vault", {"recovery_confirm": codes[-1]})
    assert done["clear"] is True and not any(c in json.dumps(done) for c in codes)
    saved = json.loads(wizard_json.read_text(encoding="utf-8"))["partial"]["vault"]
    assert "_shown_before" not in saved["answers"]
    assert eng.submit("vault", {"data_folder": "", "knowledge_folder": "", "linked": ""})["status"] == "done"
    prog = json.loads(wizard_json.read_text(encoding="utf-8"))
    assert prog["answers"]["vault"] == {k: v for k, v in prog["answers"]["vault"].items() if not k.startswith("_")}

    assert files_holding(state, codes + [PIN]) == []     # logs, wizard.json, the keyring, everything in the bot folder
    assert not any(c in caplog.text or normalize_recovery_code(c) in caplog.text for c in codes)
    assert PIN not in caplog.text
    events = json.dumps(bus.events, default=str)
    assert not any(c in events or normalize_recovery_code(c) in events for c in codes) and PIN not in events

    lines = [json.loads(x) for x in vault_of(state).log_path.read_text(encoding="utf-8").splitlines()]
    rotated = [x for x in lines if x["action"] == "recovery_rotated"]
    assert len(rotated) == 3 and all(set(x) == {"at", "action", "result"} for x in rotated)


# -- closing the window ----------------------------------------------------------------------------------------------
def test_restart_loses_nothing_and_shows_no_old_code(state, monkeypatch):
    _, eng, first = begin(state)
    second = new_code(eng)
    restart(state)

    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    resumed = kit.engine()
    screen = resumed.submit("vault", {})
    assert screen["status"] == "ask" and screen["field"]["id"] == "recovery_confirm" and "show" not in screen
    assert first not in json.dumps(screen) and second not in json.dumps(screen)
    assert kit.vault.status() == "locked"

    # Locked after a restart: a wrong PIN changes nothing.
    with monkeypatch.context() as m:
        no_rotate(m)
        assert resumed.submit("vault", {"recovery_confirm": "new"})["error"] == L.NEW_CODE_ASK_PIN_LOCKED
        assert resumed.submit("vault", {"recovery_confirm": OTHER_PIN})["error"] == L.NEW_CODE_WRONG_PIN
    assert kit.vault.status() == "locked"

    # The newest code still types back while locked; the older one does not.
    assert resumed.submit("vault", {"recovery_confirm": first})["error"] == L.CODE_MISMATCH
    assert resumed.submit("vault", {"recovery_confirm": second})["clear"] is True
    assert opens_with(state, "pin", PIN) is True
    assert opens_with(state, "recovery", second) is True


def test_restart_after_she_lost_the_code_has_a_way_forward(state):
    _, _, first = begin(state)
    restart(state)
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    resumed = kit.engine()
    second = new_code(resumed)                           # the PIN typed here opens the vault setup is finishing
    assert kit.vault.status() == "open"
    assert resumed.submit("vault", {"recovery_confirm": second})["clear"] is True
    assert vault_of(state).test_recovery(first) is False


def test_the_pin_question_does_not_survive_a_restart(state):
    kit, eng, first = begin(state)
    eng.submit("vault", {"recovery_confirm": "new"})
    screen = kit.engine().submit("vault", {})            # a new engine: back at the code question
    assert screen["field"]["label"] == "Type the recovery code back"
    assert not any(first in line for line in screen["lines"])


# -- the renderers -----------------------------------------------------------------------------------------------------
class Scripted(Terminal):
    """The real terminal renderer, answers from a script that may read what was drawn."""

    def __init__(self, engine, script):
        self.script, self.hidden = script, []
        super().__init__(engine, out=io.StringIO(), env={}, run=lambda *a, **k: None, sleep=lambda s: None,
                         tty=lambda _o: True, vt_on=lambda _o: True)

    def read(self, field, prompt):
        answer = self.script(field, self.out.getvalue())
        if field.get("hidden"):
            self.hidden.append(field["id"])
        else:
            self.out.write(str(answer) + "\n")           # a real terminal echoes what is not hidden
        return answer


def test_the_real_terminal_renderer_runs_the_step(state):
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    typed = iter(["new", OTHER_PIN, "new", PIN, "new", PIN])

    def script(field, drawn):
        answers = {"where": "1", "pin": PIN, "pin_again": PIN, "data_folder": "", "knowledge_folder": "", "linked": ""}
        if field["id"] == "recovery_confirm":
            next_one = next(typed, None)
            return next_one if next_one else CODE_SHAPE.findall(drawn)[-1]
        return answers[field["id"]]

    run = Scripted(kit.engine(), script)
    assert run.run_step("vault")["status"] == "done"

    out = run.out.getvalue()
    shown = CODE_SHAPE.findall(out)
    assert len(shown) == 3 and len(set(shown)) == 3
    assert run.hidden.count("recovery_confirm") == 7     # every answer to that question, the PIN too, is hidden
    assert PIN not in out and OTHER_PIN not in out
    assert L.NEW_CODE_WRONG_PIN in out and L.OLD_CODE_GONE in out and "not built yet" not in out
    assert not CODE_SHAPE.search(out.split(CLEAR)[-1])   # gone from the screen after the match
    v = vault_of(state)
    assert [v.test_recovery(c) for c in shown] == [False, False, True]
    assert files_holding(state, shown + [PIN, OTHER_PIN]) == []


def test_the_desktop_page_shows_only_the_newest_code(state):
    """The Core API and the Desktop's own ``logic.js``, on the real vault."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)
    first = run.post("vault", {"where": "here", "pin": PIN, "pin_again": PIN})["show"]["value"]
    assert first in run.visible()
    assert run.post("vault", {"recovery_confirm": "new"})["field"]["hidden"] is True
    assert first in run.visible()                        # the code she was shown still works here
    second = run.post("vault", {"recovery_confirm": PIN})["show"]["value"]
    page = run.visible()
    assert second in page and first not in page
    assert run.post("vault", {"recovery_confirm": second})["clear"] is True
    assert second not in run.visible() and "not built yet" not in " ".join(run.visible())
    assert PIN not in " ".join(run.visible())


def test_the_page_removes_the_old_shown_once_line_with_the_old_code():
    source = (REPO / "packages" / "desktop" / "web" / "desktop.js").read_text(encoding="utf-8")
    assert 'if (sc.clear || sc.show) wiz.querySelectorAll(".code, .code-line")' in source
    assert 'class: "line code-line", text: sc.show.line' in source


def test_the_page_stays_on_the_step_when_the_vault_has_locked(state):
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)
    first = run.post("vault", {"where": "here", "pin": PIN, "pin_again": PIN})["show"]["value"]
    vault_of(state).lock("idle")
    assert run.post("vault", {"recovery_confirm": "new"})["error"] == L.NEW_CODE_ASK_PIN_LOCKED
    second = run.post("vault", {"recovery_confirm": PIN})["show"]["value"]
    page = run.visible()
    assert second != first and second in page and first not in page and PIN not in " ".join(page)
    assert run.post("vault", {"recovery_confirm": second})["clear"] is True


def test_her_usual_unlock_also_lets_the_step_go_on(state):
    """The Desktop's Vault page and /vault unlock in the chat both send this request, to the vault setup is using."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)
    first = run.post("vault", {"where": "here", "pin": PIN, "pin_again": PIN})["show"]["value"]
    vault_of(state).lock("idle")
    opened = run.client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=DESKTOP)
    assert opened.status_code == 200
    assert run.post("vault", {"recovery_confirm": "new"})["error"] == L.NEW_CODE_ASK_PIN
    second = run.post("vault", {"recovery_confirm": PIN})["show"]["value"]
    assert second != first and run.post("vault", {"recovery_confirm": second})["clear"] is True


def test_no_wizard_reply_may_be_stored(state):
    """A reply can carry a recovery code, the first one too; the error replies get the same header."""
    kit = Kit(state)
    kit.vault = wiring.WizardVault(state)
    run = BrowserRun(kit)

    def post(step, body):
        r = run.client.post(f"/v1/wizard/{step}", json=body, headers=DESKTOP)
        assert r.headers["Cache-Control"] == "no-store"
        return r

    first = post("vault", {"answers": {"where": "here", "pin": PIN, "pin_again": PIN}, "renderer": "browser"}).json
    assert first["status"] == "show" and first["show"]["value"]
    assert post("vault", {"answers": {"recovery_confirm": "new"}}).json["status"] == "ask"
    second = post("vault", {"answers": {"recovery_confirm": PIN}}).json
    assert second["status"] == "show" and second["show"]["value"] != first["show"]["value"]
    assert run.client.get("/v1/wizard", headers=DESKTOP).headers["Cache-Control"] == "no-store"
    assert post("vault", {"answers": "not an object"}).status_code == 400
    assert post("no-such-step", {"answers": {}}).status_code == 404


def test_puffpai_setup_end_to_end_with_a_new_code(state, monkeypatch, capsys):
    """``puffpai setup`` itself: a real Core on free ports, its own wiring, the real terminal renderer."""
    from i1_e2e import free_port
    from puffpai_core.wizard import census as census_mod
    from puffpai_core.zone2 import settings as model_settings

    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server
    monkeypatch.setattr(census_mod, "run_census", lambda *a, **k: dict(CENSUS))
    monkeypatch.setattr(wiring.WizardModels, "pull", lambda self, name: None)
    monkeypatch.setattr(term_mod, "builtins", lambda core, ask, write: [])
    monkeypatch.setattr(wiring.WizardToolsmith, "plan",
                        lambda self, build, project=None: {"summary": None, "items": ["No plan this time."]})
    codes = []
    real_setup, real_rotate = arch_mod.Archivist.setup, vault_mod.Vault.rotate_recovery
    monkeypatch.setattr(arch_mod.Archivist, "setup", lambda self, pin: codes.append(real_setup(self, pin)) or codes[-1])
    monkeypatch.setattr(vault_mod.Vault, "rotate_recovery",
                        lambda self, pin: codes.append(real_rotate(self, pin)) or codes[-1])
    confirm = iter(["new", PIN])

    def read(self, field, prompt):
        fid = field["id"]
        if fid == "recovery_confirm":
            return next(confirm, None) or codes[-1]
        if fid == "model":
            return "1"
        return {"where": "1", "pin": PIN, "pin_again": PIN, "data_folder": "", "knowledge_folder": "", "linked": "", "posture": "2",
                "goal": "a birdwatching bot", "yes": "no"}.get(fid, "")
    monkeypatch.setattr(term_mod.Terminal, "read", read)
    argv = ["--port", str(free_port()), "--pages-port", str(free_port()), "--proxy-port", "0", "--exit"]

    assert term_mod.main(argv) == 1                      # not complete: no plan came; the vault step is what counts
    out = capsys.readouterr().out
    assert len(codes) == 2 and codes[0] in out and codes[1] in out and PIN not in out
    assert "not built yet" not in out
    prog = json.loads((Path(state) / "settings" / "wizard.json").read_text(encoding="utf-8"))
    assert prog["steps"]["vault"]["state"] == "done"
    assert files_holding(state, codes + [PIN]) == []
    v = vault_of(state)
    assert v.test_recovery(codes[0]) is False and v.test_recovery(codes[1]) is True
    assert seams_opens(state)


def seams_opens(state):
    from puffpai_core import seams
    return seams.check_pin(state, PIN) is None
