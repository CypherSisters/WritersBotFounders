"""Floor X6, D50 (lane L3 verifier item 5): random-looking tokens, more prefixes, split keys, one-time codes.

Each one used to reach the model text and ``store_text``. Both directions are
tested: harmless shapes (git commit id, UUID, a labelled file hash) stay readable.
"""

import hashlib
import json
import secrets
import string
import uuid

import pytest

from puffpai_core.zone1 import gate
from puffpai_core.zone1.token_rules import looks_random
from z1kit import canary_key, make


def _alnum(n: int) -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def _gated(state, text, vault_open=True):
    a, *_ = make(state, open_=vault_open)
    model, handles = gate.prepare(text, True, archivist=a)
    return model, handles.store_text


def _hidden(state, text, needles, vault_open=True):
    model, store = _gated(state, text, vault_open)
    for n in needles:
        assert n not in model, n
        assert n not in store, n
    return model, store


@pytest.mark.parametrize("vault_open", [True, False])
def test_key_split_across_lines(state, vault_open):
    key = canary_key()
    head, tail = key[:24], key[24:]
    model, _ = _hidden(state, f"my key is {head}\n{tail} thanks", [head, tail], vault_open)
    assert "thanks" in model


def test_key_wrapped_over_three_lines_in_a_file(state):
    key = "ghp_" + _alnum(40)
    _hidden(state, f"token:\n  {key[:16]}\n  {key[16:32]}\n  {key[32:]}\nnext line", [key[16:32], key[32:]])


def test_split_does_not_swallow_the_next_setting(state):
    key = canary_key()
    model, _ = _gated(state, f"A_KEY={key}\nBETA_FLAG=1\n")
    assert key not in model and "BETA_FLAG=1" in model


def test_43_char_random_token_plain_and_as_json_value(state):
    tok = secrets.token_urlsafe(32)
    _hidden(state, f"use {tok} for the build. " + json.dumps({"value": tok, "note": "hello"}), [tok])


def test_64_hex_not_labelled_as_a_hash_is_held(state):
    value = secrets.token_hex(32)
    _hidden(state, f"value {value}", [value])


def test_hugging_face_and_puffpai_tokens(state):
    hf = "hf_" + _alnum(34)
    ppt = "ppt_" + secrets.token_urlsafe(32)
    _hidden(state, f"this {hf} here and {ppt} there", [hf, ppt])


def test_keyword_next_to_a_shorter_token(state):
    tok = _alnum(12) + "7Q" + _alnum(6)
    _hidden(state, f"the api token for it is {tok}, keep it safe", [tok])


@pytest.mark.parametrize("text,code", [("your code is 482913", "482913"), ("code 482913", "482913"),
                                       ("Use code 48291373 to sign in", "48291373"),
                                       ("482913 is your verification code", "482913")])
def test_one_time_codes_near_code_are_scrubbed(state, text, code):
    a, *_ = make(state)
    model, handles = gate.prepare(text, True, archivist=a)
    assert code not in model and code not in handles.store_text
    assert any(h.kept == "scrubbed" for h in handles)


@pytest.mark.parametrize("text,keep", [("zip code 90210 is ours", "90210"), ("exit code 1234 again", "1234"),
                                       ("HTTP status code 404", "404")])
def test_numbers_that_are_not_one_time_codes_stay(state, text, keep):
    model, _ = _gated(state, text)
    assert keep in model


def test_harmless_shapes_stay_readable(state):
    sha = secrets.token_hex(20)
    uid = str(uuid.uuid4())
    file_hash = hashlib.sha256(b"installer").hexdigest()
    text = (f"merged commit {sha} into main; job {uid} finished; "
            f"sha256 of the installer: {file_hash}\n{hashlib.sha256(b'x').hexdigest()}  setup.exe\n"
            "integrity sha512-" + secrets.token_urlsafe(64))
    model, store = _gated(state, text)
    assert sha in model and uid in model and file_hash in model and "setup.exe" in model
    assert sha in store and uid in store


@pytest.mark.parametrize("benign", [
    "AbstractSingletonProxyFactoryBeanWithLongerName", "this_is_a_very_long_function_name_here_today",
    "CypherSisters/PuffPAIBuildBot/tree/main/src/puffpai", "test_lend_needs_the_vault_open_except_ticked_keys",
    "x86_64-pc-windows-msvc-toolchain-stable-release", "MicrosoftWindowsDesktopRuntime-8-win-x64-installer",
    "0000000000000000000000000000000000000000000", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef00"])
def test_long_names_and_paths_do_not_read_as_random(benign):
    assert not looks_random(benign)


def test_random_tokens_read_as_random():
    import base64
    import os
    samples = [secrets.token_urlsafe(32) for _ in range(200)] + [secrets.token_hex(32) for _ in range(50)] + \
              [base64.b64encode(os.urandom(32)).decode() for _ in range(200)] + [_alnum(40) for _ in range(200)]
    missed = [s for s in samples if not looks_random(s)]
    assert len(missed) <= 6                  # a measured guess (D50); misses well under 1 in 50
