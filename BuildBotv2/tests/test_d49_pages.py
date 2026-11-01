"""D49: package pages never act as the owner.

- A state change signed in by the device cookie must come from the Core's own
  page: its Origin (or Sec-Fetch-Site: same-origin) and the X-PuffPAI header.
- The pages address (8422) never reads the cookie and refuses owner
  credentials; a package token there has only its own rights.
- The Core's own address never serves /pkg/, and nothing may frame it.
- The Desktop frames pages from the pages address, sandboxed, with no
  allow-same-origin.
"""

import json
import re
import threading
import urllib.error
import urllib.request

import pytest

from conftest import REPO, record_install
from puffpai_core.api import create_app
from desktop_kit import DESKTOP, DESKTOP_URL, desktop_client
from puffpai_core.api.app import make_servers, stop_servers

CORE = DESKTOP_URL                     # D79: the Desktop's own origin, http://puffpai.localhost:8420
PAGES = {"puffpai.listener": "pages"}
WEB = REPO / "packages" / "desktop" / "web"


@pytest.fixture
def app(state):
    return create_app(state)


@pytest.fixture
def core(app):
    return app.extensions["puffpai"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def cookie_client(app, core):
    """A browser signed in by the device cookie at the Desktop's own address, as after puffpai pair (D79)."""
    c = desktop_client(app)
    c.sign_in(core.tokens.issue_device("browser"))
    return c


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


PEOPLE = [{"id": "sam", "name": "Sam", "role": "adult"}]


# -- the Origin / custom-header check on cookie-signed changes --------------------------
def test_cookie_change_from_another_page_is_refused(cookie_client):
    """The L5 verifier's finding: SameSite ignores ports, so a page on another 127.0.0.1 port sends the cookie."""
    for headers in ({"Origin": "http://127.0.0.1:9999", "X-PuffPAI": "1"},      # another local page
                    {"Origin": "http://127.0.0.1:8420", "X-PuffPAI": "1"},      # the old address (D79)
                    {"Origin": "http://localhost:8420", "X-PuffPAI": "1"},
                    {"Origin": "http://puffpai.localhost:18500", "X-PuffPAI": "1"},   # same name, another port
                    {"Origin": "http://127.0.0.1:8422", "X-PuffPAI": "1"},      # a package page on the pages address
                    {"Origin": "null", "X-PuffPAI": "1"},                       # a sandboxed frame
                    {"Origin": CORE},                                          # no custom header
                    {"Sec-Fetch-Site": "same-site", "X-PuffPAI": "1"},
                    {"X-PuffPAI": "1"},                                        # no Origin, no Sec-Fetch-Site
                    {}):
        for method, path, body in (("put", "/v1/people", PEOPLE),
                                   ("post", "/v1/checklist", {"id": "c", "kind": "confirm", "title": "t"}),
                                   ("delete", "/v1/notify/x", None)):
            r = getattr(cookie_client, method)(path, json=body, headers=headers)
            assert r.status_code == 403, (method, headers, r.status_code)
            assert "did not come from the Desktop" in r.json["error"]
    assert cookie_client.get("/v1/people").json == []                         # nothing changed
    assert cookie_client.get("/v1/checklist").json == []


def test_cookie_change_from_the_desktop_works(cookie_client):
    assert cookie_client.put("/v1/people", json=PEOPLE, headers=DESKTOP).status_code == 200
    same = {"Sec-Fetch-Site": "same-origin", "X-PuffPAI": "1"}                 # a browser that sent no Origin
    assert cookie_client.put("/v1/people", json=PEOPLE, headers=same).status_code == 200
    mixed = {"Origin": "http://127.0.0.1:9999", "Sec-Fetch-Site": "same-origin", "X-PuffPAI": "1"}
    assert cookie_client.put("/v1/people", json=PEOPLE, headers=mixed).status_code == 403   # Origin wins
    assert cookie_client.get("/v1/people").status_code == 200                 # reads need no header


def test_bearer_callers_are_not_affected(client, core):
    dev = bearer(core.tokens.issue_device("laptop"))
    key = bearer(core.tokens.issue_owner_key())
    for headers in (dev, key, {**dev, "Origin": "http://127.0.0.1:9999"}):
        assert client.put("/v1/people", json=PEOPLE, headers=headers).status_code == 200


def test_the_core_origin_follows_its_port(state):
    app = create_app(state, port=18420, pages_port=18422)
    c = desktop_client(app, 18420)
    c.sign_in(app.extensions["puffpai"].tokens.issue_device("b"))
    assert c.put("/v1/people", json=PEOPLE, headers=DESKTOP).status_code == 403
    assert c.put("/v1/people", json=PEOPLE, headers={"Origin": "http://puffpai.localhost:18420", "X-PuffPAI": "1"}).status_code == 200
    assert c.get("/v1/people", base_url=DESKTOP_URL).status_code == 401      # the cookie needs the Core's own port
    assert c.get("/v1/settings").json["pages"] == "http://127.0.0.1:18422"


def test_run_admin_by_cookie_needs_the_desktop_origin(cookie_client, app, monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))   # never a real UAC prompt
    monkeypatch.setattr("sys.platform", "win32")
    app.extensions["puffpai.pin_check"] = lambda pin: None if pin == "482913" else (403, "Wrong PIN.")
    sid = app.extensions["puffpai.checklist"].add({"id": "a", "kind": "run_command", "admin": True, "text": "dir",
                                                    "title": "t"}, "core", trust="core")["id"]
    body = {"factor": "pin", "pin": "482913"}
    url = f"/v1/checklist/{sid}/run-admin"
    assert cookie_client.post(url, json=body, headers={"Origin": "http://127.0.0.1:8422", "X-PuffPAI": "1"}).status_code == 403
    assert calls == []
    assert cookie_client.post(url, json=body, headers=DESKTOP).status_code == 200 and len(calls) == 1


# -- the pages address ------------------------------------------------------------------
def test_pages_address_never_takes_the_owner_login(cookie_client, client, core):
    assert cookie_client.get("/v1/checklist").status_code == 200                      # the Core's own address
    dev = core.tokens.issue_device("browser 2")
    cookie = {"Cookie": f"puffpai_device={dev}"}
    assert client.get("/v1/checklist", headers=cookie, environ_base=PAGES).status_code == 401   # cookie ignored
    assert client.get("/v1/registry", headers=cookie, environ_base=PAGES).status_code == 404    # owner routes aren't there
    r = client.put("/v1/people", json=PEOPLE, headers={**DESKTOP, **cookie}, environ_base=PAGES)
    assert r.status_code == 404 and cookie_client.get("/v1/people").json == []
    for token in (core.tokens.issue_owner_key(), core.tokens.issue_device("laptop")):
        r = client.get("/v1/checklist", headers=bearer(token), environ_base=PAGES)
        assert r.status_code == 403 and "never takes the owner" in r.json["error"]


def test_pages_address_gives_a_package_only_its_own_rights(client, core):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    assert client.get("/v1/people", headers=ft, environ_base=PAGES).status_code == 200
    assert client.get("/v1/theme", headers=ft, environ_base=PAGES).status_code == 200
    assert client.get("/v1/checklist", headers=ft, environ_base=PAGES).status_code == 200
    assert client.get("/v1/health", environ_base=PAGES).status_code == 200
    for path in ("/v1/settings", "/", "/ui/logic.js", "/v1/recover"):                 # owner and Desktop routes
        assert client.get(path, headers=ft, environ_base=PAGES).status_code == 404, path
    assert client.post("/v1/pair", json={"code": "X"}, environ_base=PAGES).status_code == 404
    assert client.put("/v1/people", json=[], headers=ft, environ_base=PAGES).status_code == 404


def test_pkg_is_only_on_the_pages_address(client, core):
    dev = bearer(core.tokens.issue_device("laptop"))
    r = client.get("/pkg/freshtable/index.html", headers=dev)
    assert r.status_code == 404 and "127.0.0.1:8422" in r.json["error"]
    r = client.get("/pkg/freshtable/x", environ_base=PAGES, base_url="http://127.0.0.1:8422")
    assert r.status_code == 302 and r.headers["Location"] == "http://freshtable.localhost:8422/x"   # D53
    r = client.get("/x", environ_base=PAGES, base_url="http://freshtable.localhost:8422")
    assert r.status_code == 404 and "Nothing installed" in r.json["error"]          # L1 forwards it (built)
    assert "frame-ancestors 'none'" in r.headers.get("Content-Security-Policy", "")  # not PuffPAI's own: never framed
    core.registry.add({"id": "freshtable", "kind": "app", "name": "FreshTable", "origin": "store:verified", "status": "running",
                       "store": {"source": "official", "repo": "https://github.com/CypherSisters/puffpai-store",
                                 "path": "freshtable", "commit": "a" * 40}})
    r = client.get("/x", environ_base=PAGES, base_url="http://freshtable.localhost:8422")
    assert CORE in r.headers["Content-Security-Policy"]                             # only the Desktop frames pages


def test_nothing_frames_the_core(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"


# -- the Desktop ------------------------------------------------------------------------
def test_desktop_frames_pages_on_their_own_origin():
    """D53: a page's frame may have allow-same-origin, since its origin is its package's own."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    frame = re.search(r'<iframe id="page-frame"[^>]*>', html).group(0)
    assert 'sandbox="allow-scripts allow-forms allow-popups allow-same-origin"' in frame
    js = (WEB / "desktop.js").read_text(encoding="utf-8") + (WEB / "logic.js").read_text(encoding="utf-8")
    assert 'const PAGE_SANDBOX = "allow-scripts allow-forms allow-popups allow-same-origin"' in js
    assert 'const WIDGET_SANDBOX = "allow-scripts"' in js                             # html widgets: still none
    assert '".localhost"' in js                                                       # pkgOrigin()
    assert 'frame.setAttribute("sandbox", L.PAGE_SANDBOX)' in js
    assert '"X-PuffPAI": "1"' in (WEB / "desktop.js").read_text(encoding="utf-8")
    assert 'credentials: "omit"' in (WEB / "desktop.js").read_text(encoding="utf-8")   # widgets: no cookie


def test_recovery_page_sends_the_desktop_header(client, cookie_client):
    page = cookie_client.get("/v1/recover").get_data(as_text=True)
    assert '"X-PuffPAI": "1"' in page
    r = cookie_client.put("/v1/settings/ui", json={"id": "desktop"},
                          headers={"Origin": CORE, "X-PuffPAI": "1"})
    assert r.status_code == 200


# -- two real listeners, random free ports, stopped at the end ---------------------------
def _get(url, headers=None, method="GET", data=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"null")


def test_two_listeners_in_one_process(state):
    app = create_app(state)
    servers = make_servers(app, port=0, pages_port=0)
    core_port, pages_port = servers[0].effective_port, servers[1].effective_port
    app.config["PUFFPAI_PAGES_ORIGIN"] = f"http://127.0.0.1:{pages_port}"   # /pkg/ checks Host against it (L1)
    assert core_port != pages_port and servers[0].effective_host == servers[1].effective_host == "127.0.0.1"
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        token = app.extensions["puffpai"].tokens.issue_device("browser")
        cookie = {"Cookie": f"puffpai_device={token}"}
        assert _get(f"http://127.0.0.1:{core_port}/v1/checklist", cookie)[0] == 401        # D79: never on 127.0.0.1
        assert _get(f"http://127.0.0.1:{core_port}/v1/checklist", bearer(token))[0] == 200
        assert _get(f"http://127.0.0.1:{core_port}/v1/checklist", {"Host": f"evil.example:{core_port}"})[0] == 421
        assert _get(f"http://127.0.0.1:{pages_port}/v1/checklist", cookie)[0] == 401      # the stamp is the server's
        spoof = {**cookie, "Host": f"127.0.0.1:{core_port}", "X-Puffpai-Listener": "core"}
        assert _get(f"http://127.0.0.1:{pages_port}/v1/checklist", spoof)[0] in (401, 421)   # headers can't change it (D79: 421)
        assert _get(f"http://127.0.0.1:{pages_port}/v1/registry", cookie)[0] == 404
        assert _get(f"http://127.0.0.1:{core_port}/pkg/x/y")[0] == 404
        status, body = _get(f"http://127.0.0.1:{pages_port}/y", {"Host": f"freshtable.localhost:{pages_port}"})
        assert status == 404 and "Nothing installed" in body["error"]                 # L1 forwards it (built, D53)
        assert _get(f"http://127.0.0.1:{pages_port}/")[0] == 404
    finally:
        stop_servers(servers)
        for t in threads:
            t.join(timeout=10)
    assert not any(t.is_alive() for t in threads)


# -- nothing loads from outside this computer (pictures would skip the Bridge) -------------------
def _csp(resp):
    policies = resp.headers.getlist("Content-Security-Policy")
    assert len(policies) == 1, policies
    return {d.split()[0]: d.split()[1:] for d in (x.strip() for x in policies[0].split(";")) if d}


def test_core_csp_keeps_pictures_and_frames_on_this_computer(cookie_client):
    for path in ("/", "/ui/logic.js", "/v1/health"):
        csp = _csp(cookie_client.get(path))
        assert csp["default-src"] == ["'self'"]
        pkgs = "http://*.localhost:8422"                                              # D53: each page's own origin
        assert csp["img-src"] == ["'self'", "http://127.0.0.1:8422", pkgs, "data:", "blob:"], path
        assert csp["frame-src"] == [pkgs] and csp["frame-ancestors"] == ["'none'"]
        assert csp["connect-src"] == ["'self'", "http://127.0.0.1:8422", pkgs]
        assert not any(v in ("*", "https:", "http:") for vals in csp.values() for v in vals)


def test_recovery_page_has_its_own_strict_policy(cookie_client):
    client = cookie_client
    r = client.get("/v1/recover")
    csp = _csp(r)
    nonce = csp["script-src"][0][len("'nonce-"):-1]
    assert csp["default-src"] == ["'none'"] and csp["frame-ancestors"] == ["'none'"]
    page = r.get_data(as_text=True)
    assert f'<script nonce="{nonce}">' in page and f'<style nonce="{nonce}">' in page
    assert "unsafe-inline" not in r.headers["Content-Security-Policy"]
    assert nonce != _csp(client.get("/v1/recover"))["script-src"][0][len("'nonce-"):-1]   # new each time


def test_pages_csp_keeps_package_pages_on_this_computer(client):
    r = client.get("/pkg/freshtable/x", environ_base=PAGES)
    csp = _csp(r)
    for key in ("default-src", "connect-src", "form-action"):
        assert csp[key] == ["'self'", "http://127.0.0.1:8422"], key
    assert csp["img-src"] == ["'self'", "http://127.0.0.1:8422", "data:", "blob:"]
    assert CORE in csp["frame-ancestors"] and "X-Frame-Options" not in r.headers


def test_a_process_ui_package_opens_on_the_pages_address(client, owner_key, state, core):
    import json as _json
    from puffpai_core.registry import row_from_manifest
    folder = state / "packages" / "voice-ui"
    folder.mkdir(parents=True)
    manifest = {"schema": "puffpai-package/1", "id": "voice-ui", "kind": "ui", "name": "Voice", "version": "0.1.0",
                "description": "Talk only.", "author": "you", "license": "MIT", "consent": "Local only.",
                "run": {"default": {"type": "command", "start": ["{python}", "app.py", "{port}"]}},
                "health": {"url": "http://127.0.0.1:{port}/", "expect": 200}}
    (folder / "puffpai.json").write_text(_json.dumps(manifest), encoding="utf-8")
    record_install(core, folder, manifest, "local:" + str(folder))
    assert client.put("/v1/settings/ui", json={"id": "voice-ui"}, headers=owner_key).status_code == 200
    r = desktop_client(core.app).get("/")
    assert r.status_code == 302 and r.headers["Location"] == "http://127.0.0.1:8422/pkg/voice-ui/"


@pytest.fixture
def owner_key(core):
    return bearer(core.tokens.issue_owner_key())
