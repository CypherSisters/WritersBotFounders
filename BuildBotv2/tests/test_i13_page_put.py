"""Lane I13 item 3: an edit on the FreshTable page reaches FreshTable through the Core's page proxy.

FreshTable's page saves with ``PUT /data`` (packages/freshtable/web/index.html, syncPush). The proxy
(``api/groups/pages.py``) forwarded only GET and POST, so every edit got 405 and was lost. It now forwards
PUT and DELETE through the same ``_forward``: the package's own origin only, its own port, its own token.

The test runs the real Core listeners (waitress, port 0) and the real FreshTable server (its own code, in
this process, with the token the Core holds for it), and sends the page's PUT over a real socket.
"""

import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from conftest import manifest_sha
from puffpai_core.api import create_app
from puffpai_core.desktop import builtin_dir
from puffpai_core.registry import row_from_manifest
from puffpai_core.steward.steward import Running

PKG = builtin_dir() / "freshtable"
TOKEN = "ppt_freshtable_test_only"


def _freshtable_module():
    spec = importlib.util.spec_from_file_location("freshtable_server_i13", PKG / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _send(port, host, path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Host": host, "Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw.decode("utf-8") or "null")
    except ValueError:
        return status, raw


@pytest.fixture
def live(state):
    """The Core's two real listeners, and FreshTable 'running' under the Steward on its own port."""
    from puffpai_core.api.app import make_servers, stop_servers
    app = create_app(state)
    core = app.extensions["puffpai"]
    servers = make_servers(app, 0, 0)
    cp, pp = (int(s.effective_port) for s in servers)
    app.config["PUFFPAI_PAGES_ORIGIN"] = f"http://127.0.0.1:{pp}"
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    manifest = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    core.installs.set("freshtable", PKG, link=True, source=str(PKG))
    core.registry.add(row_from_manifest(manifest, "core", manifest_sha=manifest_sha(PKG)))
    data = state / "data" / "freshtable"
    ft = _freshtable_module().make_server(0, data, TOKEN, None)
    ft_thread = threading.Thread(target=ft.serve_forever, daemon=True)
    ft_thread.start()
    core.steward._running["freshtable"] = Running("freshtable", "command", ft.server_address[1], TOKEN, time.time())
    try:
        yield core, pp, data
    finally:
        core.steward._running.pop("freshtable", None)
        ft.shutdown()
        ft.server_close()
        stop_servers(servers)
        for t in threads:
            t.join(timeout=5)


def test_a_page_edit_through_the_real_proxy_is_saved(live):
    core, pp, data = live
    host = f"freshtable.localhost:{pp}"
    own = {"Origin": f"http://{host}"}
    status, doc = _send(pp, host, "/data")
    assert status == 200 and doc["shoppingList"] == []
    # The page's own save: the whole document and the stamp it started from (index.html syncPush).
    edit = {**doc, "shoppingList": [{"id": "id_1", "name": "Milk", "checked": False}]}
    status, out = _send(pp, host, "/data", "PUT", edit, own)
    assert status == 200 and out["last_updated"], out
    saved = json.loads((data / "freshtable.json").read_text(encoding="utf-8"))
    assert saved["shoppingList"][0]["name"] == "Milk"                       # on disk, in FreshTable's data folder
    status, doc = _send(pp, host, "/data")
    assert status == 200 and doc["shoppingList"][0]["name"] == "Milk" and doc["last_updated"] == out["last_updated"]
    # The same page with Sec-Fetch-Site and no Origin (a same-origin fetch) is let through too.
    status, out2 = _send(pp, host, "/data", "PUT", {**doc, "pantry": [{"name": "Rice"}]},
                         {"Sec-Fetch-Site": "same-origin"})
    assert status == 200, out2
    # DELETE is forwarded under the same rules: FreshTable itself answers (it has no DELETE, so 405).
    status, _ = _send(pp, host, "/data", "DELETE", None, own)
    assert status == 405


@pytest.mark.parametrize("headers", [
    {}, {"Origin": "null"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "http://other-app.localhost:{pp}"}, {"Origin": "http://127.0.0.1:{pp}"},
])
def test_a_put_from_anywhere_else_never_reaches_freshtable(live, headers):
    core, pp, data = live
    host = f"freshtable.localhost:{pp}"
    headers = {k: v.replace("{pp}", str(pp)) for k, v in headers.items()}
    status, before = _send(pp, host, "/data")
    for method in ("PUT", "DELETE"):
        status, out = _send(pp, host, "/data", method, {**before, "shoppingList": [{"name": "planted"}]}, headers)
        assert status == 403, (method, headers, out)
    assert _send(pp, host, "/data")[1]["shoppingList"] == []                 # nothing was written
    # The old shared path takes no PUT, and the owner's own login is never taken on the pages address.
    dev = core.tokens.issue_device("Sam's laptop")
    assert _send(pp, f"127.0.0.1:{pp}", "/pkg/freshtable/data", "PUT", before,
                 {"Origin": f"http://127.0.0.1:{pp}"})[0] == 404
    assert _send(pp, host, "/data", "PUT", before, {"Origin": f"http://{host}",
                                                     "Authorization": f"Bearer {dev}"})[0] == 403


def test_a_body_over_the_cap_gets_413_and_is_never_forwarded(live, monkeypatch):
    """Verifier round 1: a 50 MB PUT was forwarded whole. Now over pages.MAX_BODY (FreshTable's own 25 MB
    limit) is refused with 413 for POST, PUT and DELETE alike; at the cap it goes through."""
    from puffpai_core.api.groups import pages
    core, pp, data = live
    host = f"freshtable.localhost:{pp}"
    own = {"Origin": f"http://{host}"}
    status, doc = _send(pp, host, "/data")
    # The real cap, over a real socket: 26 MB is refused and nothing is written.
    big = {**doc, "shoppingList": [{"name": "x" * (26 * 1024 * 1024)}]}
    assert _send(pp, host, "/data", "PUT", big, own)[0] == 413
    assert _send(pp, host, "/data")[1]["shoppingList"] == []
    # The edge, with a small cap: every sending method alike.
    monkeypatch.setattr(pages, "MAX_BODY", 1000)
    for method, path in (("PUT", "/data"), ("POST", "/api/import"), ("DELETE", "/data")):
        over = b"{" + b" " * 999 + b"}"                                     # 1001 bytes
        req = urllib.request.Request(f"http://127.0.0.1:{pp}{path}", data=over, method=method,
                                     headers={"Host": host, "Content-Type": "application/json", **own})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=15) as resp:
                code = resp.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        assert code == 413, (method, code)
    small = {**doc, "shoppingList": [{"name": "Milk"}]}
    assert len(json.dumps(small)) <= 1000
    assert _send(pp, host, "/data", "PUT", small, own)[0] == 200                 # at or under the cap: saved
    assert _send(pp, host, "/data")[1]["shoppingList"] == [{"name": "Milk"}]


def test_the_cap_reads_no_more_than_one_byte_past_it(core, monkeypatch):
    """With no Content-Length (a chunked body), the proxy reads at most MAX_BODY + 1 bytes."""
    import io
    from flask import Flask
    from puffpai_core.api.groups import pages
    monkeypatch.setattr(pages, "MAX_BODY", 10)
    app = Flask("cap")
    stream = io.BytesIO(b"y" * 1000)
    chunked = {"wsgi.input": stream, "CONTENT_LENGTH": "", "HTTP_TRANSFER_ENCODING": "chunked",
               "wsgi.input_terminated": True}                         # as waitress hands a chunked body on
    with app.test_request_context("/", method="PUT", environ_overrides=chunked):
        assert pages._capped_body() is None
    assert stream.tell() <= 11
    with app.test_request_context("/", method="PUT", data=b"y" * 10):
        assert pages._capped_body() == b"y" * 10


def test_a_static_package_takes_no_put(state):
    """A package with no server (plain files) answers 405 to PUT and DELETE, never a file."""
    app = create_app(state)
    core = app.extensions["puffpai"]
    folder = builtin_dir() / "example-page"
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    core.installs.set("example-page", folder, link=True, source=str(folder))
    core.registry.add(row_from_manifest(manifest, "core", manifest_sha=manifest_sha(folder)))
    c = app.test_client()
    on = {"base_url": "http://example-page.localhost:8422", "environ_base": {"puffpai.listener": "pages"}}
    own = {"Origin": "http://example-page.localhost:8422"}
    assert c.get("/index.html", **on).status_code == 200
    for method in ("put", "delete"):
        r = getattr(c, method)("/index.html", data=b"x", headers=own, **on)
        assert r.status_code == 405 and b"<" not in r.data, method
