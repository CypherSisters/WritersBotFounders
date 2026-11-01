"""Lane D137: ``puffpai apikey new | list | revoke``, program keys (``ppk_``, label ``PROGRAM_LABEL``) for Home
Assistant and other programs; they open only the chat routes for programs (tests/test_program_keys.py). The key is
printed once, by ``new``; ``list`` shows program keys' labels and dates only; ``revoke`` takes back every program key
with exactly that label and nothing else (never a plain owner key). No Core runs in these tests: the command works
on ``<state>/tokens.db`` directly."""

from __future__ import annotations

import re

import pytest

from puffpai_core import cli
from puffpai_core.api.auth import PROGRAM_LABEL, TokenStore

KEY = re.compile(r"ppk_[A-Za-z0-9_-]{20,}")


def _store(state) -> TokenStore:
    return TokenStore(state / "tokens.db")


def _new(capsys, *extra) -> tuple[int, str]:
    code = cli.main(["apikey", "new", *extra])
    return code, capsys.readouterr().out


def test_new_prints_the_key_once_and_it_is_a_program_key(state, capsys):
    code, out = _new(capsys, "--label", "home assistant")
    assert code == 0
    keys = KEY.findall(out)
    assert len(keys) == 1 and out.count(keys[0]) == 1
    assert cli.APIKEY_KEEP in out
    caller = _store(state).lookup(keys[0])
    assert caller is not None
    assert (caller.kind, caller.via, caller.label) == ("owner", "api_key", PROGRAM_LABEL + "home assistant")
    assert caller.program and not caller.needs_second_half
    assert "/openai/v1 and /ollama/api" in cli.APIKEY_KEEP
    raw = (state / "tokens.db").read_bytes()
    assert keys[0].encode() not in raw                     # only its SHA-256 is kept


def test_new_without_a_label_gets_a_dated_one(state, capsys):
    code, out = _new(capsys)
    assert code == 0
    [row] = _store(state).program_keys()
    assert re.fullmatch(r"program key \d{4}-\d{2}-\d{2}", row["label"])
    assert row["label"] in out


def test_list_shows_labels_and_dates_never_a_key(state, capsys):
    assert cli.main(["apikey", "list"]) == 0
    assert capsys.readouterr().out.strip() == cli.APIKEY_NONE
    _, out = _new(capsys, "--label", "kitchen tablet")
    key = KEY.findall(out)[0]
    _store(state).issue_device("paired browser: test")      # a device token is not a program key
    _store(state).issue_owner_key("plain owner key")        # nor is a plain owner key: never listed
    assert cli.main(["apikey", "list"]) == 0
    listed = capsys.readouterr().out
    assert "kitchen tablet" in listed
    assert re.search(r"\d{4}-\d{2}-\d{2}", listed)
    assert key not in listed and "ppk_" not in listed and "ppd_" not in listed
    assert "plain owner key" not in listed
    assert "paired browser" not in listed and PROGRAM_LABEL not in listed
    rows = _store(state).program_keys()
    assert rows == [{"label": "kitchen tablet", "created_at": rows[0]["created_at"]}]


def test_revoke_takes_back_only_that_label(state, capsys):
    store = _store(state)
    gone_a = store.issue_program_key("old script")
    gone_b = store.issue_program_key("old script")
    kept = store.issue_program_key("home assistant")
    device = store.issue_device("old script")              # same words, but a paired device: never touched
    owner = store.issue_owner_key("old script")            # same words, but a plain owner key: never touched
    assert cli.main(["apikey", "revoke", "old script"]) == 0
    out = capsys.readouterr().out
    assert "Took back 2 program keys" in out
    assert store.lookup(gone_a) is None and store.lookup(gone_b) is None
    assert store.lookup(kept) is not None
    assert store.lookup(device) is not None and store.lookup(device).via == "device"
    assert store.lookup(owner) is not None and not store.lookup(owner).program
    assert cli.main(["apikey", "revoke", "old script"]) == 1
    assert "Took back 0 program keys" in capsys.readouterr().out


@pytest.mark.parametrize("label", ["two\nlines", "carriage\rreturn", "x" * 61, "   ", "bell\x07"])
def test_a_bad_label_is_refused(state, capsys, label):
    code, out = _new(capsys, "--label", label)
    assert code == 2
    assert "ppk_" not in out
    assert _store(state).program_keys() == []


def test_sixty_characters_is_fine(state, capsys):
    code, _ = _new(capsys, "--label", "y" * 60)
    assert code == 0
    assert _store(state).program_keys()[0]["label"] == "y" * 60


def test_labels_go_with_the_right_action(state, capsys):
    assert cli.main(["apikey", "new", "positional"]) == 2
    assert cli.main(["apikey", "list", "--label", "x"]) == 2
    assert cli.main(["apikey", "revoke"]) == 2
    capsys.readouterr()
    assert _store(state).program_keys() == []
