"""Program keys (``puffpai apikey``, ``auth.PROGRAM_LABEL``): they open only the chat routes for programs.

A program key may call routes with access ``public`` and ``program`` (``/openai/v1`` and ``/ollama/api``) and
nothing else: no Yes card (the wizard's plan card, a consent card), no Registry, no chat, no chat history. Every
other owner credential (a paired device, a plain owner key) calls the program routes as it calls any owner route;
a package token is refused there. Through the Flask test client with a stub brain (tests/test_api_openai.py)."""

from __future__ import annotations

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.auth import PROGRAM_LABEL, PROGRAM_LIMIT, TokenStore
from test_api_openai import ASK, StubBrain

PROGRAM_ROUTES = [("get", "/openai/v1/models", None), ("post", "/openai/v1/chat/completions", ASK),
                  ("get", "/ollama/api/tags", None), ("post", "/ollama/api/chat", ASK)]
OWNER_ROUTES = [("get", "/v1/registry", None), ("post", "/v1/chat", {"text": "hello"}),
                ("post", "/v1/wizard/plan", {"answers": {"yes": True}}), ("get", "/v1/chat/history", None),
                ("post", "/v1/consent/abc123/answer", {"answer": "yes"})]


@pytest.fixture
def core(state):
    app = create_app(state)
    brain = StubBrain()
    app.extensions["puffpai_brain"] = brain
    return app, app.extensions["puffpai"].tokens, brain


def _bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


def _call(client, method, path, body, headers=None):
    return getattr(client, method)(path, headers=headers or {}, json=body)


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_a_program_key_opens_the_program_routes(core, method, path, body):
    app, tokens, _brain = core
    key = _bearer(tokens.issue_program_key("home assistant"))
    assert _call(app.test_client(), method, path, body, key).status_code == 200


def test_a_program_key_reaches_the_brain(core):
    app, tokens, brain = core
    key = _bearer(tokens.issue_program_key("home assistant"))
    r = app.test_client().post("/ollama/api/chat", headers=key, json=ASK)
    assert r.status_code == 200 and brain.calls and brain.calls[-1]["personal"] is False


@pytest.mark.parametrize("method,path,body", OWNER_ROUTES)
def test_a_program_key_is_refused_everywhere_else(core, method, path, body):
    app, tokens, brain = core
    key = _bearer(tokens.issue_program_key("home assistant"))
    r = _call(app.test_client(), method, path, body, key)
    assert r.status_code == 403, (path, r.status_code)
    assert r.json == {"error": PROGRAM_LIMIT}
    assert brain.calls == []


def test_the_refusal_is_plain():
    assert PROGRAM_LIMIT == ("A program key can call only the chat routes for programs "
                             "(/openai/v1 and /ollama/api).")


def test_public_routes_stay_open_to_a_program_key(core):
    app, tokens, _brain = core
    key = _bearer(tokens.issue_program_key("home assistant"))
    assert app.test_client().get("/v1/health", headers=key).status_code == 200


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_no_credential_is_401(core, method, path, body):
    app, _tokens, _brain = core
    assert _call(app.test_client(), method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_a_paired_device_and_a_plain_owner_key_still_open_them(core, method, path, body):
    app, tokens, _brain = core
    client = app.test_client()
    device = _bearer(tokens.issue_device("test device"))
    owner = _bearer(tokens.issue_owner_key("test owner key"))
    assert _call(client, method, path, body, device).status_code == 200
    assert _call(client, method, path, body, owner).status_code == 200


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_a_package_token_is_refused_there(core, method, path, body):
    app, tokens, brain = core
    package = _bearer(tokens.issue_package_token("freshtable"))
    assert _call(app.test_client(), method, path, body, package).status_code == 403
    assert brain.calls == []


def test_a_plain_owner_key_is_not_a_program_key(core):
    app, tokens, _brain = core
    owner = tokens.issue_owner_key("old script")
    assert not tokens.lookup(owner).program
    assert app.test_client().get("/v1/registry", headers=_bearer(owner)).status_code == 200


def test_program_keys_lists_only_program_keys(state):
    tokens = TokenStore(state / "tokens.db")
    tokens.issue_owner_key("plain owner key")
    tokens.issue_owner_key(PROGRAM_LABEL[:-1])               # close to the prefix, but not it
    tokens.issue_device("a device")
    tokens.issue_package_token("freshtable")
    tokens.issue_program_key("home assistant")
    rows = tokens.program_keys()
    assert [r["label"] for r in rows] == ["home assistant"]
    assert set(rows[0]) == {"label", "created_at"}


def test_revoke_by_label_leaves_other_keys(state):
    tokens = TokenStore(state / "tokens.db")
    gone = tokens.issue_program_key("old script")
    kept = tokens.issue_program_key("home assistant")
    owner = tokens.issue_owner_key("old script")             # same words, a plain owner key
    owner_prefixed = tokens.issue_owner_key("old script x")
    assert tokens.revoke_program_key("old script") == 1
    assert tokens.lookup(gone) is None
    assert tokens.lookup(kept) is not None and tokens.lookup(kept).program
    assert tokens.lookup(owner) is not None and tokens.lookup(owner_prefixed) is not None
    assert tokens.revoke_program_key(PROGRAM_LABEL + "home assistant") == 0   # the prefix is added once only
    assert tokens.lookup(kept) is not None
