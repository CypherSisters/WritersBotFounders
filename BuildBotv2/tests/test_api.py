import re

import pytest
import yaml

from conftest import REPO
from puffpai_core.api import create_app
from puffpai_core.registry import row_from_manifest

OPENAPI = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "delete", "patch"}
BUILT_LANES = {"L0", "L5"}


@pytest.fixture
def app(state):
    return create_app(state)


@pytest.fixture
def core(app):
    return app.extensions["puffpai"]


@pytest.fixture
def client(app):
    return app.test_client()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner(core):
    return bearer(core.tokens.issue_owner_key())


@pytest.fixture
def device(core):
    return bearer(core.tokens.issue_device("Sam's laptop"))


@pytest.fixture
def installed(core, freshtable):
    core.registry.add(row_from_manifest(freshtable, "made"))
    core.registry.add({"id": "weather", "kind": "tool", "name": "Weather", "origin": "made",
                       "status": "stopped", "health": {"ok": False, "checked_at": None, "reason": "Not checked yet."}})


def test_health_is_public(client):
    r = client.get("/v1/health")
    assert r.status_code == 200 and r.json["ok"] is True and r.json["api"] == "v1"


def test_registry_needs_a_credential(client, installed):
    r = client.get("/v1/registry")
    assert r.status_code == 401 and "Sign in first" in r.json["error"]
    assert client.get("/v1/registry", headers=bearer("ppk_made-up")).status_code == 401


def test_registry_for_the_owner(client, owner, installed):
    rows = client.get("/v1/registry", headers=owner).json
    assert [r["id"] for r in rows] == ["freshtable", "weather"]
    assert [r["id"] for r in client.get("/v1/registry?kind=tool", headers=owner).json] == ["weather"]
    assert client.get("/v1/registry/freshtable", headers={"X-PuffPAI-Key": owner["Authorization"][7:]}).json["name"] == "FreshTable"
    r = client.get("/v1/registry/nope", headers=owner)
    assert r.status_code == 404 and "nope" in r.json["error"]


def test_package_token_scope(client, core, installed):
    tok = bearer(core.tokens.issue_package_token("freshtable"))
    assert client.get("/v1/registry/freshtable", headers=tok).status_code == 200
    for path in ("/v1/registry/weather", "/v1/registry"):
        r = client.get(path, headers=tok)
        assert r.status_code == 403 and "A package token can call only" in r.json["error"]
    assert client.post("/v1/chat", headers=tok).status_code == 403
    assert client.post("/v1/vault/lock", headers=tok).status_code == 403
    for method, path in (("post", "/v1/notify"), ("get", "/v1/theme"),
                         ("get", "/v1/checklist"), ("get", "/v1/events?timeout=0")):
        assert getattr(client, method)(path, headers=tok).status_code not in (401, 403), path
    assert client.post("/v1/brain/chat", headers=tok, json={}).status_code == 400   # allowed in, body checked


def test_new_package_token_replaces_the_old(client, core, installed):
    old = core.tokens.issue_package_token("freshtable")
    new = core.tokens.issue_package_token("freshtable")
    assert client.get("/v1/registry/freshtable", headers=bearer(old)).status_code == 401
    assert client.get("/v1/registry/freshtable", headers=bearer(new)).status_code == 200


def test_cookie_is_for_paired_devices_only(core, installed):
    from desktop_kit import desktop_client
    client = desktop_client(core.app)                    # D79: the cookie counts at puffpai.localhost only
    client.sign_in(core.tokens.issue_package_token("freshtable"))
    assert client.get("/v1/registry/freshtable").status_code == 401
    client.sign_in(core.tokens.issue_device("kiosk"))
    assert client.get("/v1/registry").status_code == 200


def test_owner_device_and_core_routes(client, owner, device):
    r = client.post("/v1/vault/unlock", headers=owner)
    assert r.status_code == 403 and "paired device" in r.json["error"]
    assert client.post("/v1/vault/unlock", headers=device).status_code == 409   # built (L3): no vault yet
    assert client.post("/v1/vault/hold", headers=device).status_code == 403


def test_tokens_are_stored_hashed(core, state):
    token = core.tokens.issue_owner_key()
    assert token.encode() not in (state / "tokens.db").read_bytes()


def test_unknown_route_is_plain(client, owner):
    r = client.get("/v1/nothing", headers=owner)
    assert r.status_code == 404 and "openapi.yaml" in r.json["error"]


def _flask_routes(app):
    out = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        path = re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", r"{\1}", rule.rule)
        view = app.view_functions[rule.endpoint]
        for m in rule.methods - {"HEAD", "OPTIONS"}:
            out[(m.lower(), path)] = (view.puffpai_access, getattr(view, "puffpai_lane", None),
                                      getattr(view, "puffpai_built_by", "L0"))
    return out


def _openapi_routes():
    out = {}
    for path, item in OPENAPI["paths"].items():
        for m, op in item.items():
            if m in METHODS:
                out[(m, path)] = (op["x-access"], op["x-lane"])
    return out


def test_routes_match_openapi(app):
    flask, spec = _flask_routes(app), _openapi_routes()
    assert set(flask) == set(spec)
    for key, (access, lane, built_by) in flask.items():
        assert access == spec[key][0], key
        if lane is not None:                       # a stub: its lane must be the one in the contract
            assert lane == spec[key][1], key
        else:                                      # built (any lane): the contract documents a real answer, not 501 only
            path, method = key[1], key[0]
            codes = set(OPENAPI["paths"][path][method]["responses"])
            assert codes - {"501"}, key


def test_every_stub_answers_501_with_its_lane(app, client, device):
    for (method, path), (access, lane, _built) in _flask_routes(app).items():
        if lane is None or access == "core":
            continue
        url = re.sub(r"\{[a-z_]+\}", "x", path)
        if path.startswith("/pkg/"):                 # pages live on the pages address only (D49)
            assert getattr(client, method)(url, headers=device).status_code == 404
            r = getattr(client, method)(url, environ_base={"puffpai.listener": "pages"})
        else:
            r = getattr(client, method)(url, headers=device)
        assert r.status_code == 501, (method, url, r.status_code)
        assert r.json == {"error": "not built yet", "lane": lane}
