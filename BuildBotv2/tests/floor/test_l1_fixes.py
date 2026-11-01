"""Floor items from the L1 verifier pass (10-22), one regression test or more each.

1. D49 and D53 (with lane L5's gate): every package page on its own origin
   ``http://<id>.localhost:8422``, never with the owner's login; one package's page can't read or
   post to another or to the Core; a ``null``-origin post is refused; an unknown package Host is
   404; cookie-signed state changes need the Core's own origin and ``X-PuffPAI: 1``; the Core's
   cookie never reaches a package, and credentials are stripped both ways.
2. X24: a git source's committed .venv / node_modules never run and never hide from the hash.
3. X17: where a package listens is checked on every Caretaker tick; unreadable is unknown.
4. The terminal key is bound to one Core run.
5. Yes cards left from the last run expire at start; staging is emptied.
6. D41: Stop ends the package's key leases (Archivist ``end_holder``).
Plus what lane L4's bypass watch calls on Steward: ``running()``, ``pids()``, ``stop(id, reason)``.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import HELLO, _double, git_init, manifest_sha, wait_for, yes
from puffpai_core.api import create_app
from puffpai_core.api.app import TERMINAL_LABEL, start_core, stop_core
from puffpai_core.packages import fsutil
from puffpai_core.registry import row_from_manifest
from puffpai_core.steward import jobs, ports
from puffpai_core.steward.jobs import pid_alive
from puffpai_core.steward.steward import Running, StewardError, _setup_program_problem

CORE_ORIGIN = "http://puffpai.localhost:18419"   # conftest's core fixture: create_app(port=18419); the Desktop's name (D79)
PAGE_ORIGIN = "http://127.0.0.1:8422"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(core):
    return core.app.test_client()


@pytest.fixture
def cookie_client(core):
    from desktop_kit import desktop_client
    c = desktop_client(core.app, 18419)              # D79: the cookie counts at puffpai.localhost only
    c.sign_in(core.tokens.issue_device("Sam's laptop"))
    return c


# -- 1. D49 + D53 (L5's gate is the base; every package page has its own origin) -------------
PAGES_ENV = {"environ_base": {"puffpai.listener": "pages"}}
PAGES = {"base_url": PAGE_ORIGIN, **PAGES_ENV}
A, B = "pkg-a", "pkg-b"
A_ORIGIN, B_ORIGIN = f"http://{A}.localhost:8422", f"http://{B}.localhost:8422"


def on(id):
    """A request to package *id*'s own origin on the pages listener (D53)."""
    return {"base_url": f"http://{id}.localhost:8422", **PAGES_ENV}


def test_pkg_is_refused_on_the_core_address(core, client, cookie_client):
    owner = bearer(core.tokens.issue_owner_key())
    for c, h in ((client, owner), (cookie_client, {})):
        for path in ("/pkg/hello-tool/", "/pkg/hello-tool/x"):
            r = c.get(path, headers=h)
            assert r.status_code == 404 and "pages address" in r.json["error"]
        assert c.post("/pkg/hello-tool/x", headers=h).status_code == 404
    # A package origin's Host on the Core's own listener is just the Core (no package routing there).
    assert client.get("/v1/health", base_url=A_ORIGIN).status_code == 200


def test_cookie_state_change_needs_core_origin_and_header(core, cookie_client, drawbridge, hello_copy):
    body = {"source": str(hello_copy)}
    # A package page's JavaScript (its own origin, or 8422), a form post, or no proof: refused.
    for headers in ({}, {"X-PuffPAI": "1"}, {"Origin": PAGE_ORIGIN, "X-PuffPAI": "1"},
                    {"Origin": A_ORIGIN, "X-PuffPAI": "1"},
                    {"Origin": CORE_ORIGIN}, {"Origin": "null", "X-PuffPAI": "1"},
                    {"Sec-Fetch-Site": "same-site", "X-PuffPAI": "1"}, {"Origin": CORE_ORIGIN, "X-PuffPAI": "0"}):
        r = cookie_client.post("/v1/packages", json=body, headers=headers)
        assert r.status_code == 403 and "did not come from the Desktop" in r.json["error"], headers
    assert core.consent.list(status="pending") == []
    # From the Core's own page: goes ahead.
    r = cookie_client.post("/v1/packages", json=body, headers={"Origin": CORE_ORIGIN, "X-PuffPAI": "1"})
    assert r.status_code == 202
    cid = r.json["consent_id"]
    r = cookie_client.post(f"/v1/consent/{cid}/answer", json={"answer": "yes", "factor": "tap"},
                           headers={"Origin": A_ORIGIN, "X-PuffPAI": "1"})
    assert r.status_code == 403 and core.consent.get(cid)["status"] == "pending"
    r = cookie_client.post(f"/v1/consent/{cid}/answer", json={"answer": "no", "factor": "tap"},
                           headers={"Sec-Fetch-Site": "same-origin", "X-PuffPAI": "1"})
    assert r.status_code == 200 and r.json["status"] == "declined"
    # Reading by cookie needs no header, but a package origin never gets to read it (no CORS).
    r = cookie_client.get("/v1/consent", headers={"Origin": A_ORIGIN})
    assert r.status_code == 200 and "Access-Control-Allow-Origin" not in r.headers
    assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    dev = bearer(core.tokens.issue_device("kiosk"))
    assert core.app.test_client().post("/v1/packages", json={}, headers=dev).status_code == 400


def test_pages_address_never_honours_the_owner(core, freshtable):
    core.registry.add(row_from_manifest(freshtable, "made"))
    pages = core.app.test_client()
    pages.set_cookie("puffpai_device", core.tokens.issue_device("Sam's laptop"))
    owner = bearer(core.tokens.issue_owner_key())
    dev = bearer(core.tokens.issue_device("phone"))
    pkg = bearer(core.tokens.issue_package_token("freshtable"))
    for method, path in (("post", "/v1/packages"), ("post", "/v1/consent/c1/answer"), ("get", "/v1/consent"),
                         ("get", "/v1/registry"), ("post", "/v1/packages/freshtable/stop"),
                         ("post", "/v1/packages/freshtable/open/main")):
        for h in ({}, owner, dev, pkg):
            r = getattr(pages, method)(path, headers=h, **PAGES)
            assert r.status_code in (403, 404), (method, path, h, r.status_code)
    assert core.consent.list() == []
    # The routes a package token may call: only with that token, never the owner's.
    assert pages.get("/v1/registry/freshtable", **PAGES).status_code == 401          # the cookie is ignored
    assert pages.get("/v1/registry/freshtable", headers=owner, **PAGES).status_code == 403
    assert pages.get("/v1/registry/freshtable", headers=dev, **PAGES).status_code == 403
    assert pages.get("/v1/registry/freshtable", headers=pkg, **PAGES).status_code == 200


_REDIRECTS = {"/go": "/login", "/rel": "next?x=1", "/abs": "http://pkg-a.localhost:8422/home",
              "/away": "https://evil.example/x", "/proto": "//evil.example/x", "/bs": "/\\evil.example/x",
              "/port": "http://127.0.0.1:{port}/x", "/js": "javascript:alert(1)"}


class _FakePackage(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)                    # read the body, or Windows may reset the connection
        self.seen.append({"port": self.server.server_address[1], "path": self.path, "headers": dict(self.headers)})
        if self.path in _REDIRECTS:
            self.send_response(302)
            self.send_header("Location", _REDIRECTS[self.path].replace("{port}", str(self.server.server_address[1])))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        for k, v in (("Set-Cookie", "puffpai_device=planted; Path=/"), ("Set-Cookie", "x=1"),
                     ("Clear-Site-Data", '"cookies"'), ("WWW-Authenticate", "Basic"),
                     ("Access-Control-Allow-Origin", "*"),
                     ("Access-Control-Allow-Credentials", "true"), ("Authorization", "Bearer leak"),
                     ("X-Kept", "yes"), ("Content-Type", "text/plain")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b"page")

    do_GET = do_POST = do_PUT = do_DELETE = _answer

    def log_message(self, *a):
        pass


@pytest.fixture
def two_packages(core, tmp_path):
    """Two 'running' packages, pkg-a and pkg-b, each on a local fake page server (no real process)."""
    servers = {}
    for id in (A, B):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakePackage)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        folder = tmp_path / id
        shutil.copytree(HELLO, folder)
        manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
        manifest["id"] = id
        (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
        core.installs.set(id, folder, link=True, source=str(folder))
        core.registry.add(row_from_manifest(manifest, "made", manifest_sha=manifest_sha(folder)))
        core.steward._running[id] = Running(id, "command", srv.server_address[1], f"ppt_{id}", time.time())
        servers[id] = srv
    _FakePackage.seen = []
    yield core, {id: srv.server_address[1] for id, srv in servers.items()}
    for id, srv in servers.items():
        core.steward._running.pop(id, None)
        srv.shutdown()
        srv.server_close()


def test_each_package_page_has_its_own_origin(two_packages):
    core, ports_of = two_packages
    c = core.app.test_client()
    r = c.get("/notes?x=1", **on(A))
    assert r.status_code == 200 and r.data == b"page"
    seen = _FakePackage.seen[-1]
    assert seen["port"] == ports_of[A] and seen["path"] == "/notes?x=1"
    assert seen["headers"]["Authorization"] == f"Bearer ppt_{A}"
    csp = r.headers["Content-Security-Policy"]
    assert f"connect-src 'self' {A_ORIGIN};" in csp and f"form-action 'self' {A_ORIGIN};" in csp
    assert "127.0.0.1:8422" not in csp.split("frame-ancestors")[0]                  # not the shared pages address
    # A package origin never reaches the Core API: /v1/... there is the package's own path.
    r = c.post("/v1/packages", json={}, headers={"Origin": A_ORIGIN}, **on(A))
    assert r.status_code == 200 and _FakePackage.seen[-1]["path"] == "/v1/packages"
    assert _FakePackage.seen[-1]["port"] == ports_of[A] and core.consent.list() == []
    # The old shared path redirects to the package's own origin (GET only).
    r = c.get(f"/pkg/{A}/notes?x=1", **PAGES)
    assert r.status_code == 302 and r.headers["Location"] == f"{A_ORIGIN}/notes?x=1"
    assert c.post(f"/pkg/{A}/notes", headers={"Origin": A_ORIGIN}, **PAGES).status_code == 404
    assert c.get(f"/pkg/{A}/notes", base_url="http://localhost:8422", **PAGES_ENV).status_code == 403


def test_a_page_cant_post_to_another_package_or_from_null(two_packages):
    """D53, D55: a state change reaches a package only from that package's own origin."""
    core, ports_of = two_packages
    c = core.app.test_client()
    before = len(_FakePackage.seen)
    for origin in ("null", A_ORIGIN, CORE_ORIGIN, PAGE_ORIGIN, "https://evil.example",
                   "http://pkg-b.localhost:9999", "http://pkg-b.localhost"):
        for method in ("post", "put", "patch", "delete"):
            r = getattr(c, method)("/save", data=b"x", headers={"Origin": origin}, **on(B))
            assert r.status_code in (403, 405), (origin, method)                      # PUT, DELETE: no route at all
    assert c.post("/save", data=b"x", **on(B)).status_code == 403                       # no Origin, no proof
    assert c.post("/save", data=b"x", headers={"Sec-Fetch-Site": "cross-site"}, **on(B)).status_code == 403
    assert len(_FakePackage.seen) == before                                             # nothing reached B
    r = c.post("/save", data=b"x", headers={"Origin": B_ORIGIN}, **on(B))
    assert r.status_code == 200 and _FakePackage.seen[-1]["port"] == ports_of[B]
    assert c.post("/", data=b"x", headers={"Sec-Fetch-Site": "same-origin"}, **on(B)).status_code == 200


def test_a_page_cant_read_another_package(two_packages):
    core, _ = two_packages
    c = core.app.test_client()
    for origin in (A_ORIGIN, "null", PAGE_ORIGIN, "https://evil.example"):
        r = c.get("/data.json", headers={"Origin": origin}, **on(B))
        assert "Access-Control-Allow-Origin" not in r.headers, origin                   # the browser won't let it read
    # Only the Desktop's own origin gets widget JSON across origins, never with credentials.
    r = c.get("/w.json", headers={"Origin": CORE_ORIGIN}, **on(B))
    assert r.headers["Access-Control-Allow-Origin"] == CORE_ORIGIN
    assert "Access-Control-Allow-Credentials" not in r.headers


def test_unknown_or_bad_package_hosts_get_404(two_packages):
    core, _ = two_packages
    c = core.app.test_client()
    r = c.get("/", **on("not-installed"))
    assert r.status_code == 404 and "Nothing installed" in r.json["error"]
    for host in ("http://x.localhost:8422", "http://Bad_ID.localhost:8422", "http://pkg-a.localhost:9999",
                 "http://a.b.localhost:8422", "http://pkg-a.localhost"):
        assert c.get("/", base_url=host, **PAGES_ENV).status_code == 404, host
    assert len(_FakePackage.seen) == 0


def test_the_core_cookie_never_reaches_a_package(two_packages):
    core, _ = two_packages
    c = core.app.test_client()
    dev = core.tokens.issue_device("Sam's laptop")
    # An owner credential sent to a package origin is refused outright (L5's gate, D49).
    r = c.post("/save", data=b"x", headers={"Origin": A_ORIGIN, "Authorization": f"Bearer {dev}"}, **on(A))
    assert r.status_code == 403 and len(_FakePackage.seen) == 0
    r = c.post("/save", data=b"x", headers={"Origin": A_ORIGIN, "Cookie": f"puffpai_device={dev}; other=1",
                                            "X-PuffPAI-Key": "ppk_x", "X-PuffPAI": "1"}, **on(A))
    assert r.status_code == 200 and r.data == b"page" and r.headers.get("X-Kept") == "yes"
    for name in ("Set-Cookie", "Clear-Site-Data", "WWW-Authenticate", "Access-Control-Allow-Origin",
                 "Access-Control-Allow-Credentials", "Authorization"):
        assert name not in r.headers, name
    assert "frame-ancestors" in r.headers.get("Content-Security-Policy", "")          # the gate's header stays
    sent = _FakePackage.seen[-1]["headers"]
    assert sent["Authorization"] == f"Bearer ppt_{A}"
    assert not {"Cookie", "X-PuffPAI-Key", "X-PuffPAI"} & set(sent)
    assert dev not in json.dumps(sent)


def test_redirects_stay_on_the_packages_own_origin(two_packages):
    """D53: a relative or own-origin redirect goes back unchanged; anywhere else is refused; no /pkg/ prefix."""
    core, ports_of = two_packages
    c = core.app.test_client()
    r = c.get("/go", **on(A))
    assert r.status_code == 302 and r.headers["Location"] == "/login"
    sent = _FakePackage.seen[-1]["headers"]
    assert sent["Host"] == f"{A}.localhost:8422"                                       # told its own origin
    assert "X-Forwarded-Prefix" not in sent
    r = c.get("/login", **on(A))
    assert r.status_code == 200 and _FakePackage.seen[-1]["path"] == "/login"           # reaches it as /login
    assert _FakePackage.seen[-1]["port"] == ports_of[A]
    assert c.get("/rel", **on(A)).headers["Location"] == "next?x=1"
    assert c.get("/abs", **on(A)).headers["Location"] == "http://pkg-a.localhost:8422/home"
    for path in ("/away", "/proto", "/bs", "/port", "/js"):
        r = c.get(path, **on(A))
        assert r.status_code == 502 and "Location" not in r.headers, path
    r = c.get("/abs", **on(B))                                                         # A's origin, seen from B
    assert r.status_code == 502


def test_every_answer_names_the_card_it_answers(core, drawbridge, hello_copy, tmp_path, monkeypatch, capsys):
    """A Yes is bound to the card shown (card_sha): refused without it or with another card's."""
    from puffpai_core.consent import ConsentError, card_sha
    staged = core.packages.add(str(hello_copy))
    cid = staged["consent_id"]
    assert staged["card_sha"] == card_sha(staged["card"]) == core.consent.get(cid)["card_sha"]
    for bad in (None, "", "0" * 64):
        with pytest.raises(ConsentError) as e:
            core.consent.answer(cid, "yes", "tap", by="test", card_sha=bad)
        assert e.value.status in (400, 409)
    with pytest.raises(ConsentError) as e:                                            # a No is checked too
        core.consent.answer(cid, "no", "tap", by="test", card_sha="0" * 64)
    assert e.value.status == 409 and core.consent.get(cid)["status"] == "pending"
    dev = bearer(core.tokens.issue_device("Sam's laptop"))
    c = core.app.test_client()
    r = c.post(f"/v1/consent/{cid}/answer", json={"answer": "yes", "factor": "tap"}, headers=dev)
    assert r.status_code == 400 and "card_sha" in r.json["error"]
    r = c.post(f"/v1/consent/{cid}/answer", json={"answer": "yes", "factor": "tap", "card_sha": staged["card_sha"]},
               headers=dev)
    assert r.status_code == 200 and r.json["status"] == "granted"
    assert core.packages.wait(cid, 300)["status"] == "installed"
    # The CLI's own Yes path (no Core running) sends the shown card's hash too.
    from puffpai_core import cli
    from puffpai_core.consent import ConsentStore
    sent = []
    real = ConsentStore.answer
    monkeypatch.setattr(ConsentStore, "answer", lambda self, *a, **k: (sent.append(k.get("card_sha")),
                                                                       real(self, *a, **k))[1])
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    other = tmp_path / "other"
    shutil.copytree(HELLO, other)
    m = json.loads((other / "puffpai.json").read_text(encoding="utf-8"))
    m["id"] = "hello-two"
    (other / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_STATE", str(tmp_path / "cli-state"))
    assert cli.main(["add", str(other)]) == 0
    assert sent and sent[-1] and len(sent[-1]) == 64
    assert "Installed hello-two" in capsys.readouterr().out


def test_the_cli_sends_card_sha_through_a_running_core(monkeypatch, capsys):
    from puffpai_core import cli
    from puffpai_core.consent import card_sha
    card = {"title": "Add Hello Tool?", "name": "Hello Tool", "version": "1.0.0", "kind": "tool", "origin": "local:x",
            "id": "hello-tool"}
    calls = []

    def fake_call(info, method, path, body=None):
        calls.append((method, path, body))
        if path == "/v1/packages":
            return 202, {"consent_id": "c1", "id": "hello-tool", "card": card, "floor": False}
        if path.endswith("/answer"):
            return 200, {"status": "declined"}
        return 404, {}
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "ppd_x"})
    monkeypatch.setattr(cli, "_call", fake_call)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli.main(["add", "somewhere"]) == 0
    answer = [b for m, p, b in calls if p.endswith("/answer")][0]
    assert answer["card_sha"] == card_sha(card) and answer["answer"] == "no"


def test_d55_package_ids_have_no_underscore(core, hello_copy):
    from puffpai_core.packages import manifest as manifest_mod
    data = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    for bad in ("my_app", "a", "1abc", "Abc", "a.b"):
        data["id"] = bad
        (hello_copy / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(manifest_mod.ManifestError):
            manifest_mod.load(hello_copy)
    data["id"] = "my-app"
    (hello_copy / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
    assert manifest_mod.load(hello_copy)[0]["id"] == "my-app"
    c = core.app.test_client()
    assert c.get("/", base_url="http://my_app.localhost:8422", **PAGES_ENV).status_code == 404
    r = c.get("/", **on("my-app"))
    assert r.status_code == 404 and "Nothing installed" in r.json["error"]


def test_open_gives_the_packages_own_origin(core, freshtable):
    freshtable["ui"]["pages"] = [{"id": "main", "title": "FreshTable", "path": "/", "show": "in_desktop"}]
    core.registry.add(row_from_manifest(freshtable, "store:verified", store={
        "source": "official", "repo": "https://github.com/CypherSisters/puffpai-store", "path": "freshtable", "commit": "a" * 40}))
    owner = bearer(core.tokens.issue_owner_key())
    r = core.app.test_client().post("/v1/packages/freshtable/open/main", headers=owner)
    assert r.status_code == 200 and r.json == {"open": "in_desktop", "url": "http://freshtable.localhost:8422/"}


def test_open_sends_a_third_party_page_to_its_own_tab(core, freshtable):
    freshtable["ui"]["pages"] = [{"id": "main", "title": "FreshTable", "path": "/", "show": "in_desktop"}]
    core.registry.add(row_from_manifest(freshtable, "github:https://github.com/x/y"))
    owner = bearer(core.tokens.issue_owner_key())
    r = core.app.test_client().post("/v1/packages/freshtable/open/main", headers=owner)
    assert r.status_code == 200 and r.json == {"open": "new_tab", "url": "http://freshtable.localhost:8422/"}


def _http(port, path, headers=None, method="GET"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {}, method=method,
                                 data=b"{}" if method == "POST" else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_two_real_listeners_split_by_socket(state):
    from puffpai_core.api.app import make_servers, stop_servers
    app = create_app(state)
    core = app.extensions["puffpai"]
    servers = make_servers(app, 0, 0)
    cp, pp = (int(s.effective_port) for s in servers)
    app.config["PUFFPAI_PAGES_ORIGIN"] = f"http://127.0.0.1:{pp}"
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        assert cp != pp
        dev = core.tokens.issue_device("Sam's laptop")
        cookie = {"Cookie": f"puffpai_device={dev}"}
        assert _http(cp, "/v1/health") == 200
        assert _http(cp, "/pkg/x/") == 404
        assert _http(pp, "/v1/consent", cookie) == 404
        assert _http(pp, "/v1/consent", {"Authorization": f"Bearer {dev}"}) == 403
        assert _http(pp, "/v1/packages", {**cookie, "X-PuffPAI": "1", "Origin": f"http://127.0.0.1:{pp}"},
                     "POST") == 404
        assert _http(pp, "/", {"Host": f"nothing-here.localhost:{pp}"}) == 404           # D53: no such package
        assert _http(pp, "/v1/consent", {**cookie, "Host": f"nothing-here.localhost:{pp}"}) == 404
        assert _http(cp, "/v1/health", {"Host": f"pkg-a.localhost:{cp}"}) == 421          # D79: the Core listener answers its own names only
        assert _http(cp, "/v1/health", {"Host": f"puffpai.localhost:{cp}"}) == 200
        assert core.consent.list() == []
    finally:
        stop_servers(servers)
        for t in threads:
            t.join(timeout=5)


# -- 2. X24 --------------------------------------------------------------------------
def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_source_committed_venv_never_runs(core, drawbridge, tmp_path, monkeypatch):
    seen_files = []
    from conftest import drawbridge_double

    def check_package(path, manifest):
        seen_files.append(sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()))
        return {"verdict": "allow", "reasons": [], "rows": []}
    drawbridge_double(monkeypatch, check_package)
    repo = tmp_path / "repo"
    shutil.copytree(HELLO, repo)
    marker = tmp_path / "ran.txt"
    m = json.loads((repo / "puffpai.json").read_text(encoding="utf-8"))
    m["id"] = "gitpkg"
    m["run"]["windows"] = {"type": "command", "python": True, "setup": ["{python} probe_setup.py"],
                           "start": [sys.executable, "server.py"]}
    m["run"]["default"] = m["run"]["windows"]
    (repo / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    (repo / "probe_setup.py").write_text(f"import sys\nopen({str(marker)!r}, 'a').write(sys.executable)\n",
                                         encoding="utf-8")
    planted = repo / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    planted.parent.mkdir(parents=True)
    planted.write_bytes(b"MZ not a real python")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / "evil.js").write_text("// unchecked", encoding="utf-8")
    git_init(repo)                       # fails the test at once if init fails (conftest)
    _git(repo, "add", "-A", "-f")
    assert _git(repo, "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qm", "x").returncode == 0
    assert ".venv/" in _git(repo, "ls-files").stdout
    staged = core.packages.add("file:///" + str(repo).replace("\\", "/"))
    folder = next((core.state / "packages" / ".staging").iterdir())
    assert fsutil.regenerated(folder) == []
    assert not any(".venv" in f or "node_modules" in f or f.startswith(".git/") for f in seen_files[-1])
    assert yes(core, staged)["status"] == "installed"
    ran = marker.read_text(encoding="utf-8")
    final = core.state / "packages" / "gitpkg"
    assert ran and os.path.normcase(str(final / ".venv")) in os.path.normcase(ran)   # Steward's own venv
    assert (final / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).read_bytes()[:20] != \
        b"MZ not a real python"
    assert not (final / "node_modules").exists()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_git_source_case_variant_venv_never_runs(core, drawbridge, tmp_path):
    """X24: a committed .Venv (any case) with a .pth in site-packages is deleted from staging and never runs."""
    repo = tmp_path / "repo"
    shutil.copytree(HELLO, repo)
    marker = tmp_path / "pth-ran.txt"
    m = json.loads((repo / "puffpai.json").read_text(encoding="utf-8"))
    m["id"] = "casepkg"
    m["run"]["windows"] = {"type": "command", "python": True, "setup": ["{python} -c pass"],
                           "start": [sys.executable, "server.py"]}
    m["run"]["default"] = m["run"]["windows"]
    (repo / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    site = repo / ".Venv" / "Lib" / "site-packages"
    site.mkdir(parents=True)
    (site / "evil.pth").write_text(f"import os; open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    (repo / ".Venv" / "pyvenv.cfg").write_text("home = nowhere\n", encoding="utf-8")
    (repo / "Node_Modules").mkdir()
    (repo / "Node_Modules" / "evil.js").write_text("// unchecked", encoding="utf-8")
    git_init(repo)                       # fails the test at once if init fails (conftest)
    _git(repo, "add", "-A", "-f")
    assert _git(repo, "-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qm", "x").returncode == 0
    assert ".Venv/Lib/site-packages/evil.pth" in _git(repo, "ls-files").stdout
    staged = core.packages.add("file:///" + str(repo).replace("\\", "/"))
    folder = next((core.state / "packages" / ".staging").iterdir())
    assert len(folder.name) == 8
    assert fsutil.regenerated(folder) == [] and not any(p.name.lower() in (".venv", "node_modules")
                                                        for p in folder.iterdir())
    assert yes(core, staged)["status"] == "installed"
    assert not marker.exists()                                                         # the .pth never ran
    final = core.state / "packages" / "casepkg"
    assert not (final / ".venv" / "Lib" / "site-packages" / "evil.pth").exists()


def test_case_variants_are_skipped_everywhere(tmp_path):
    src = tmp_path / "src"
    for name in (".Venv", "NODE_MODULES", "__PyCache__", ".GIT"):
        (src / "sub" / name).mkdir(parents=True)
        (src / "sub" / name / "x.txt").write_text("x", encoding="utf-8")
    (src / "keep.txt").write_text("k", encoding="utf-8")
    assert fsutil.regenerated(src) == ["sub/.GIT", "sub/.Venv", "sub/NODE_MODULES", "sub/__PyCache__"]
    before = fsutil.tree_hash(src)
    (src / "sub" / ".Venv" / "x.txt").write_text("changed", encoding="utf-8")
    assert fsutil.tree_hash(src) == before                                             # never hashed, so stripped
    dst = tmp_path / "dst"
    fsutil.copy_package(src, dst)
    assert sorted(p.relative_to(dst).as_posix() for p in dst.rglob("*") if p.is_file()) == ["keep.txt"]
    assert fsutil.strip_regenerated(src) and fsutil.regenerated(src) == []
    vp = src / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert "not in the checked files" in _setup_program_problem([str(src / "NODE_MODULES" / "x.exe")], src, {},
                                                                {"python": True})
    assert _setup_program_problem([str(vp)], src, {}, {"python": True}) is None


def test_setup_refuses_a_case_variant_venv_planted_after_the_strip(core, drawbridge, hello_copy, monkeypatch):
    core.packages.start_after_install = False
    staged = core.packages.add(str(hello_copy))
    real_setup = core.steward.setup

    def plant_then_setup(id, expect_tree=None):
        (core.state / "packages" / id / ".VENV" / "Lib").mkdir(parents=True)
        return real_setup(id, expect_tree=expect_tree)
    monkeypatch.setattr(core.steward, "setup", plant_then_setup)
    result = yes(core, staged)
    assert result["status"] == "failed" and ".VENV" in result["problems"][0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_a_deep_git_clone_over_260_characters_stages(core, tmp_path, monkeypatch):
    seen = []
    from conftest import drawbridge_double

    def check_package(path, manifest):
        seen.extend(p.name for p in Path(fsutil.long_path(path)).rglob("*") if p.is_file())
        return {"verdict": "allow", "reasons": [], "rows": []}
    drawbridge_double(monkeypatch, check_package)
    repo = tmp_path / "deep"
    shutil.copytree(HELLO, repo)
    deep = Path(fsutil.long_path(repo)) / ("d" * 60) / ("e" * 60) / ("f" * 60) / ("g" * 60)
    deep.mkdir(parents=True)
    (deep / "deep-file.txt").write_text("deep", encoding="utf-8")
    m = json.loads((repo / "puffpai.json").read_text(encoding="utf-8"))
    m["id"] = "deeppkg"
    (repo / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    g = lambda *a: subprocess.run(["git", "-c", "core.longpaths=true", "-C", str(repo), *a], capture_output=True,
                                  text=True, check=False)
    git_init(repo, pre=("-c", "core.longpaths=true"))
    g("add", "-A")
    assert g("-c", "user.name=t", "-c", "user.email=t@t.invalid", "commit", "-qm", "x").returncode == 0
    staged = core.packages.add("file:///" + str(repo).replace("\\", "/"))
    folder = next((core.state / "packages" / ".staging").iterdir())
    longest = max(len(str(p)) for p in Path(fsutil.long_path(folder)).rglob("*")) - len("\\\\?\\")
    assert longest > 260 and "deep-file.txt" in seen
    core.consent.answer(staged["consent_id"], "no", "tap", by="test")
    assert core.packages.wait(staged["consent_id"], 60)["status"] == "declined"
    assert list((core.state / "packages" / ".staging").iterdir()) == []                # deleted, long path and all


def test_setup_refuses_files_that_appear_after_the_check(core, drawbridge, hello_copy, monkeypatch):
    core.packages.start_after_install = False
    staged = core.packages.add(str(hello_copy))
    folder = next((core.state / "packages" / ".staging").iterdir())
    real_setup = core.steward.setup

    def plant_then_setup(id, expect_tree=None):
        (core.state / "packages" / id / "node_modules").mkdir()
        (core.state / "packages" / id / "node_modules" / "late.js").write_text("x", encoding="utf-8")
        return real_setup(id, expect_tree=expect_tree)
    monkeypatch.setattr(core.steward, "setup", plant_then_setup)
    result = yes(core, staged)
    assert result["status"] == "failed" and "appeared in its folder after its check" in result["problems"][0]
    assert folder.name  # staged folder was used


def test_setup_refuses_a_changed_tree(core, drawbridge, hello_copy, monkeypatch):
    core.packages.start_after_install = False
    staged = core.packages.add(str(hello_copy))
    real_setup = core.steward.setup

    def edit_then_setup(id, expect_tree=None):
        (core.state / "packages" / id / "server.py").write_text("print('swapped')\n", encoding="utf-8")
        return real_setup(id, expect_tree=expect_tree)
    monkeypatch.setattr(core.steward, "setup", edit_then_setup)
    result = yes(core, staged)
    assert result["status"] == "failed" and "changed after its check" in result["problems"][0]


def test_setup_program_must_be_in_the_checked_tree(tmp_path):
    folder = tmp_path / "pkg"
    (folder / "node_modules" / ".bin").mkdir(parents=True)
    (folder / "tool.exe").write_bytes(b"x")
    block = {"python": True}
    vp = folder / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert _setup_program_problem([str(vp), "x.py"], folder, {}, block) is None
    assert _setup_program_problem(["npm", "install"], folder, {}, block) is None
    assert _setup_program_problem([sys.executable, "-V"], folder, {}, block) is None
    assert _setup_program_problem([str(folder / "tool.exe")], folder, {}, block) is None
    assert "not in the checked files" in _setup_program_problem(
        [str(folder / "node_modules" / ".bin" / "x.exe")], folder, {}, block)
    assert "not in the checked files" in _setup_program_problem([str(vp)], folder, {}, {"python": False})


# -- 3. X17 every tick ----------------------------------------------------------------
@pytest.fixture
def running(core, drawbridge, hello_copy):
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    assert core.registry.get("hello-tool")["status"] == "running"
    return core


def test_a_later_all_networks_bind_is_stopped(running, monkeypatch):
    ct = running.caretaker
    late = []
    monkeypatch.setattr(ports, "job_listeners",
                        lambda pids: [("127.0.0.1", 18555)] + ([("0.0.0.0", 18556)] if late else []))
    wait_for(lambda: running.caretaker.check("hello-tool")["health"]["ok"], 60, 0.5)   # after-start check done
    ct.tick()
    ct.tick()
    assert running.steward.alive("hello-tool") and ct.cards == []
    late.append(True)                                     # it binds 0.0.0.0 later
    ct.tick()
    row = running.registry.get("hello-tool")
    assert row["status"] == "broken" and "0.0.0.0:18556" in row["health"]["reason"]
    assert not running.steward.alive("hello-tool")
    assert ct.cards[-1]["kind"] == "exposed" and ct.cards[-1]["buttons"] == ["start_again", "remove"]


def test_a_lan_bind_is_stopped(running, monkeypatch):
    monkeypatch.setattr(ports, "job_listeners", lambda pids: [("192.168.1.20", 18557)])
    assert running.caretaker.watch_listeners() == ["hello-tool"]
    assert not running.steward.alive("hello-tool")


def test_unreadable_listeners_are_unknown_not_clean(running, monkeypatch):
    ct = running.caretaker
    got = []
    ct.on_card.append(got.append)
    monkeypatch.setattr(ports, "job_listeners", lambda pids: None)
    for _ in range(2):
        ct.watch_listeners()
    assert got == []
    ct.watch_listeners()
    assert len(got) == 1 and got[0]["kind"] == "listen_unknown" and "3 times in a row" in got[0]["line"]
    ct.watch_listeners()
    assert len(got) == 1                                  # one card per run of unknowns
    monkeypatch.setattr(ports, "job_listeners", lambda pids: [("127.0.0.1", 1)])
    ct.watch_listeners()
    assert ct._unknown.get("hello-tool") is None
    assert running.steward.alive("hello-tool")


def test_listen_table_failure_is_none(monkeypatch):
    assert ports.listen_table() is not None               # this computer's table reads
    if os.name == "nt":
        def broken(*_a, **_k):
            raise OSError(5, "denied")
        monkeypatch.setattr(ports, "_win_listen_table", broken)
    else:
        def broken(*_a, **_k):
            raise OSError(13, "denied")
        monkeypatch.setattr(ports, "tcp_rows", broken)
        monkeypatch.setattr(ports, "_ss_listen_table", lambda: None)
    assert ports.listen_table() is None
    assert ports.job_listeners([os.getpid()]) is None
    assert ports.job_listeners([]) == []


# -- 4. terminal key bound to one Core run ---------------------------------------------
def test_terminal_key_is_bound_to_one_core_run(state):
    app1 = create_app(state, port=18419)
    core1 = app1.extensions["puffpai"]
    core1.proxy_port = 0                           # a free port, never the real 8421 (conftest guard)
    old = start_core(core1)
    try:
        c1 = app1.test_client()
        assert c1.get("/v1/consent", headers=bearer(old)).status_code == 200
        # A crash: nothing ran stop_core, terminal.json is still there.
        core1.caretaker.stop()
        assert (state / "settings" / "terminal.json").is_file()
        app2 = create_app(state, port=18419)
        core2 = app2.extensions["puffpai"]
        core2.proxy_port = 0
        new = start_core(core2)
        try:
            c2 = app2.test_client()
            assert c2.get("/v1/consent", headers=bearer(old)).status_code == 401
            assert c2.get("/v1/consent", headers=bearer(new)).status_code == 200
            assert json.loads((state / "settings" / "terminal.json").read_text())["token"] == new
            # A terminal-labelled key from another run is refused even if it is in the store.
            stray = core2.tokens.issue_device(f"{TERMINAL_LABEL} (run 0000)")
            assert c2.get("/v1/consent", headers=bearer(stray)).status_code == 401
            # The terminal taps everyday cards; a floor card refuses its tap (D29).
            cid = core2.consent.request({"kind": "package", "target": "x"}, {"title": "t"}, True)
            sha = core2.consent.get(cid)["card_sha"]
            r = c2.post(f"/v1/consent/{cid}/answer", json={"answer": "yes", "factor": "tap", "card_sha": sha},
                        headers=bearer(new))
            assert r.status_code == 403
        finally:
            stop_core(core2, new)
        assert app2.test_client().get("/v1/consent", headers=bearer(new)).status_code == 401
        assert not (state / "settings" / "terminal.json").exists()
    finally:
        core1.steward.stop_all(remember=False)


# -- 5. cards from the last run expire ---------------------------------------------------
def test_waiting_cards_expire_after_a_restart(state, drawbridge, hello_copy):
    app1 = create_app(state, port=18419)
    core1 = app1.extensions["puffpai"]
    staged = core1.packages.add(str(hello_copy))
    swap = core1.consent.request({"kind": "provider_swap", "target": "x"}, {"title": "Use x?"}, False)
    other = core1.consent.request({"kind": "host", "target": "x"}, {"title": "Send?"}, False)   # a contract kind (events.json)
    (state / "packages" / ".staging" / "leftover").mkdir()
    assert len(list((state / "packages" / ".staging").iterdir())) == 2
    app2 = create_app(state, port=18419)                   # the Core started again
    core2 = app2.extensions["puffpai"]
    assert core2.packages.recover_after_restart()["expired"] == [staged["consent_id"]]
    assert core2.providers.recover_after_restart() == [swap]
    rec = core2.consent.get(staged["consent_id"])
    assert rec["status"] == "expired" and "expired when BuildBot restarted" in rec["note"]
    assert core2.consent.get(other)["status"] == "pending"            # not an install card
    assert list((state / "packages" / ".staging").iterdir()) == []
    dev = bearer(core2.tokens.issue_device("Sam's laptop"))
    r = app2.test_client().post(f"/v1/consent/{staged['consent_id']}/answer", json={"answer": "yes"}, headers=dev)
    assert r.status_code == 409 and "Add it again" in r.json["error"]
    assert core2.registry.get("hello-tool") is None


# -- 6. D41: Stop ends key leases ----------------------------------------------------------
def test_stop_ends_the_packages_key_leases(running, monkeypatch):
    """D41 with lane L3's real Archivist: a key lent at start (ticked, D27) is ended by Stop."""
    from conftest import FakeMachine
    from puffpai_core import seams
    from puffpai_core.zone1 import archivist as mod
    from z1kit import PIN, canary_key
    a = mod.for_state(running.state, running.bus)
    assert a is mod.for_state(running.state) and a.bus is running.bus               # the Core's one Archivist
    a.vault.machine = FakeMachine()                                                # never real DPAPI in tests
    a.setup(PIN)
    key = canary_key()
    a.put_secret("HELLO_KEY", key, unattended=True)
    assert seams.lease_for_start(running.state, "hello-tool", ["HELLO_KEY"], running.bus) == {"HELLO_KEY": key}
    assert [x for x in a.leases() if "hello-tool" in str(x)]
    ended = []
    real = mod.Archivist.end_holder
    monkeypatch.setattr(mod.Archivist, "end_holder", lambda self, borrower: (ended.append(borrower),
                                                                              real(self, borrower))[1])
    running.steward.stop("hello-tool")
    assert ended == [mod.PACKAGE_HOLDER + "hello-tool"]
    assert not [x for x in a.leases() if "hello-tool" in str(x)]
    assert "key leases ended: 1" in running.steward.log_path("hello-tool").read_text(encoding="utf-8")
    assert seams.verify_pin(running.state, PIN, running.bus) is True
    assert seams.verify_pin(running.state, "Wrong#pin99", running.bus) is False


def test_stop_without_or_with_a_broken_archivist_still_stops(running, monkeypatch):
    from puffpai_core import seams
    from puffpai_core.zone1 import archivist as mod
    log = running.steward.log_path("hello-tool")
    real_find = seams.find
    monkeypatch.setattr(seams, "find", lambda name: None if name.startswith("zone1.archivist.") else real_find(name))
    assert running.steward.stop("hello-tool") is True                                # no Archivist at all
    assert "none to end (the vault is not built yet)" in log.read_text(encoding="utf-8")
    monkeypatch.setattr(seams, "find", real_find)
    running.steward.start("hello-tool")

    def boom(self, borrower):
        raise RuntimeError("vault broke")
    monkeypatch.setattr(mod.Archivist, "end_holder", boom)                         # L3's real name, failing
    assert running.steward.stop("hello-tool") is True
    assert "Archivist could not end them (RuntimeError)" in log.read_text(encoding="utf-8")


# -- what lane L4's bypass watch calls on Steward -------------------------------------------
def test_steward_running_and_stop_with_a_reason(running):
    s = running.steward
    assert s.running() == ["hello-tool"]
    pids = s.pids("hello-tool")
    assert pids and all(isinstance(p, int) for p in pids)
    assert s.stop("hello-tool", "Reached the internet around the Bridge") is True
    assert s.running() == [] and not any(pid_alive(p) for p in pids)
    row = running.registry.get("hello-tool")
    assert row["status"] == "stopped"                       # a reason is never a status
    assert "(Reached the internet around the Bridge)" in s.log_path("hello-tool").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt", reason="the venv launcher is a Windows thing")
def test_pids_include_the_interpreter_behind_a_venv_launcher(core, tmp_path):
    venv = tmp_path / "venv"
    from puffpai_core.steward.steward import base_python
    subprocess.run([base_python(), "-m", "venv", "--without-pip", str(venv)], check=True, capture_output=True,
                   timeout=120)
    launcher = venv / "Scripts" / "python.exe"
    pid_file = tmp_path / "pid.txt"
    job = jobs.Job.spawn([str(launcher), "-c",
                          f"import os,time; open({str(pid_file)!r},'w').write(str(os.getpid())); time.sleep(120)"],
                         tmp_path, dict(os.environ), tmp_path / "log.txt")
    assert job.contained
    core.steward._running["venvpkg"] = Running("venvpkg", "command", None, None, time.time(), job=job)
    try:
        real = int(wait_for(lambda: pid_file.is_file() and pid_file.read_text().strip(), 30))
        pids = core.steward.pids("venvpkg")
        assert real in pids and job.pid in pids
        assert core.steward.running() == ["venvpkg"]
    finally:
        core.steward.stop("venvpkg", "test over", status=None)
    assert not pid_alive(real) and not pid_alive(job.pid)


def test_an_uncontained_start_is_refused(running, monkeypatch):
    running.steward.stop("hello-tool")
    monkeypatch.setattr(jobs.Job, "contained", property(lambda self: False))
    monkeypatch.setattr(jobs, "IS_WINDOWS", True)
    with pytest.raises(StewardError, match="could not be held in a job"):
        running.steward.start("hello-tool")
    assert running.steward.running() == []
    row = running.registry.get("hello-tool")
    assert row["status"] == "broken" and "could not be held in a job" in row["health"]["reason"]


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_process_that_cant_join_its_job_never_runs(tmp_path, monkeypatch):
    """Lane I4a: no job, no run. The process is ended while still suspended, by its own handle (never a
    tree walk by PID), so it never ran a line, and Steward refuses it."""
    k32, nt = jobs._win()

    class NoAssign:
        def __getattr__(self, name):
            return getattr(k32, name)

        @staticmethod
        def AssignProcessToJobObject(job, handle):
            return 0
    monkeypatch.setattr(jobs, "_win", lambda: (NoAssign(), nt))
    ran = tmp_path / "ran.txt"
    job = jobs.Job.spawn([sys.executable, "-c", f"open({str(ran)!r}, 'w').write('ran')"], tmp_path,
                         dict(os.environ), None)
    assert job.contained is False
    assert job.proc.poll() is not None                # already ended
    time.sleep(0.5)
    assert not ran.exists()                           # it never ran
