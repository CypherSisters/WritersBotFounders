"""D79: the Desktop and its cookie live at http://puffpai.localhost:<port>, never on 127.0.0.1.

A browser does not split cookies by port (RFC 6265 8.5), and SameSite ignores ports too, so a cookie set on
127.0.0.1:<Core port> reaches every adopted app's server on 127.0.0.1:<its port>. These tests run the real
listeners on free ports (never 8420-8422) and use a real cookie jar (``http.cookiejar``) as the browser:
host-only cookies stay on their own name (``DomainStrictNonDomain``, as browsers do), ports are ignored, and
``*.localhost`` names connect to 127.0.0.1 the way browsers resolve them themselves (Python never resolves
``puffpai.localhost``). The jar is also made to accept ``Domain=localhost`` from a ``*.localhost`` page,
the worst case for cookie tossing.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import http.server
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from puffpai_core import pairing
from puffpai_core.api.app import create_app, make_servers, run_servers
from puffpai_core.api.auth import cookie_name
from puffpai_core.packages import manifest as manifest_mod

COOKIE = "puffpai_device"                     # the plain name: these Cores run on free ports, so theirs is cookie_name(port) (D129)
DEVICE_HEADER = "X-PuffPAI-Device"            # the second half of her login (D79), from localStorage
DEVICE_KEY = "puffpai.device_key"             # desktop.js's localStorage key


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in (8420, 8421, 8422):
            return port


# -- the browser ---------------------------------------------------------------------------------------------
class _Loopback(http.client.HTTPConnection):
    """Any name (``puffpai.localhost``, ``app.localhost``) connects to 127.0.0.1, as a browser does for
    ``*.localhost``; the ``Host`` header and the cookie jar still see the name."""

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", self.port), self.timeout)


class _LoopbackHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_Loopback, req)


class _BrowserPolicy(http.cookiejar.DefaultCookiePolicy):
    """Host-only cookies stay host-only; and, the worst case, ``Domain=localhost`` set by a page on any
    ``*.localhost`` name is accepted (and then sent to ``puffpai.localhost``)."""

    def __init__(self):
        super().__init__(strict_ns_domain=http.cookiejar.DefaultCookiePolicy.DomainStrictNonDomain)

    def set_ok_domain(self, cookie, request):
        host = urllib.parse.urlsplit(request.get_full_url()).hostname or ""
        if cookie.domain_specified and cookie.domain in (".localhost", "localhost") and host.endswith(".localhost"):
            return True
        return super().set_ok_domain(cookie, request)


class Browser:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=_BrowserPolicy())
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _LoopbackHandler(),
                                                  urllib.request.HTTPCookieProcessor(self.jar))
        # localStorage: one store per exact origin (scheme, host and port), unlike cookies.
        self.storage: dict[str, dict[str, str]] = {}

    def page_call(self, origin, path, method="GET", body=None):
        """What desktop.js on *origin* sends: X-PuffPAI: 1, its Origin, and the device key from that origin's
        own localStorage (none on any other origin)."""
        headers = {"Origin": origin, "X-PuffPAI": "1"}
        key = self.storage.get(origin, {}).get(DEVICE_KEY)
        if key:
            headers[DEVICE_HEADER] = key
        return self.open(f"{origin}{path}", method, body, headers)

    def open(self, url, method="GET", body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, r.geturl(), r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.geturl(), e.read()

    def cookies(self, name=COOKIE):
        """The device cookies in the jar, under the plain name or a port's own (D129)."""
        return [(c.domain, c.path, c.value) for c in self.jar if c.name == name or c.name.startswith(f"{name}_")]

    def desktop_sign_in(self, core_url: str, state) -> tuple[str, str]:
        """What she does: open the pairing link (the old one, to show where it lands), then the Desktop's own
        page trades the code for the cookie, from its own origin. Returns (Desktop origin, token)."""
        code = pairing.new_code(state)
        status, final, _ = self.open(f"{core_url}/")
        assert status == 200, status
        origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(final))
        status, _, body = self.open(f"{origin}/v1/pair", "POST", {"code": code},
                                    {"Origin": origin, "X-PuffPAI": "1"})
        assert status == 200, status
        key = json.loads(body).get("device_key")                      # the pairing test asserts its shape
        if key:
            self.storage.setdefault(origin, {})[DEVICE_KEY] = key    # desktop.js: localStorage.setItem
        values = [v for _d, _p, v in self.cookies()]
        assert not key or key not in values                           # the second half is never a cookie
        assert len(values) == 1
        return origin, values[0]


# -- a fake adopted app: records every request's headers, and can plant cookies ------------------------------
class FakeApp:
    def __init__(self):
        self.seen: list[dict] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append({k.lower(): v for k, v in self.headers.items()})
                self.send_response(200)
                if self.path.startswith("/plant"):
                    self.send_header("Set-Cookie", f"{COOKIE}=x; Path=/")
                    self.send_header("Set-Cookie", f"{COOKIE}=x; Domain=localhost; Path=/v1")
                    self.send_header("Set-Cookie", f"{COOKIE}=y; Domain=localhost; Path=/")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def core(tmp_path):
    port, pages_port = _free_port(), _free_port()
    app = create_app(tmp_path / "state", port=port, pages_port=pages_port)
    servers = make_servers(app, port, pages_port)
    stop = threading.Event()
    t = threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, daemon=True)
    t.start()
    yield app, port, pages_port
    stop.set()
    t.join(timeout=10)


@pytest.fixture
def fake_app():
    a = FakeApp()
    yield a
    a.close()


def _raw(port, path, host, method="GET", headers=None, body: bytes | None = None):
    """One request to 127.0.0.1:<port> with exactly this ``Host``: (status, headers, Set-Cookie list, body)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        if host:
            c.putheader("Host", host)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        pairs = r.getheaders()
        return (r.status, {k.lower(): v for k, v in pairs},
                [v for k, v in pairs if k.lower() == "set-cookie"], r.read())
    finally:
        c.close()


# -- 1. the cookie never reaches an app ----------------------------------------------------------------------
def test_cookie_never_reaches_an_adopted_app(core, fake_app):
    app, port, _pp = core
    browser = Browser()
    origin, token = browser.desktop_sign_in(f"http://127.0.0.1:{port}", app.extensions["puffpai"].state)
    assert browser.page_call(origin, "/v1/settings")[0] == 200          # signed in at the Desktop
    # She opens her adopted apps in tabs, by every address an app gets.
    for url in (f"http://127.0.0.1:{fake_app.port}/", f"http://localhost:{fake_app.port}/",
                f"http://myapp.localhost:{fake_app.port}/"):
        assert browser.open(url)[0] == 200
    assert len(fake_app.seen) == 3
    for headers in fake_app.seen:
        assert COOKIE not in headers.get("cookie", ""), headers
    # Where it went instead: the old link landed on the Desktop's own name, and the cookie is host-only there.
    assert origin == f"http://puffpai.localhost:{port}"
    assert app.extensions["puffpai"].tokens.lookup(token).via == "device"
    assert browser.cookies() == [("puffpai.localhost", "/", token)]


# -- 2. cookie tossing and overwriting ---------------------------------------------------------------------
def test_planted_cookies_never_sign_out_or_replace_the_desktop(core, fake_app):
    app, port, _pp = core
    tokens = app.extensions["puffpai"].tokens
    browser = Browser()
    origin, token = browser.desktop_sign_in(f"http://127.0.0.1:{port}", app.extensions["puffpai"].state)
    # The app plants puffpai_device host-only and with Domain=localhost, from 127.0.0.1 and from a
    # *.localhost name.
    assert browser.open(f"http://127.0.0.1:{fake_app.port}/plant")[0] == 200
    assert browser.open(f"http://evil.localhost:{fake_app.port}/plant")[0] == 200
    planted = [c for c in browser.cookies() if c[2] != token]
    assert any(d == ".localhost" for d, _p, _v in planted)              # the worst case is really in the jar
    # The Desktop's own cookie is unchanged.
    assert ("puffpai.localhost", "/", token) in browser.cookies()
    # The browser now sends the planted ones too, the /v1 one first; she stays signed in.
    status, _, body = browser.page_call(origin, "/v1/settings")
    assert status == 200, body
    status, _, body = browser.page_call(origin, "/v1/settings/ui", "PUT", {"id": "desktop"})
    assert status != 401 and status != 403, body
    assert tokens.lookup(token).via == "device"


def test_every_cookie_is_checked_not_only_the_first(core):
    app, port, _pp = core
    token = app.extensions["puffpai"].tokens.issue_device("browser")
    key = app.extensions["puffpai"].tokens.bind_device(token)
    host = f"puffpai.localhost:{port}"
    for cookie, want in ((f"{COOKIE}=x; {COOKIE}={token}", 200), (f"{COOKIE}={token}; {COOKIE}=x", 200),
                         (f"{COOKIE}=x; {COOKIE}=y", 401), (f"{COOKIE}=x", 401)):
        assert _raw(port, "/v1/settings", host, headers={"Cookie": cookie, DEVICE_HEADER: key})[0] == want, cookie
    # The login that counts (a Developer shell belongs to it) is the real token, not the planted first one.
    from puffpai_core.api.app import _cookie_caller
    from werkzeug.http import parse_cookie
    caller, used = _cookie_caller(app.extensions["puffpai"], parse_cookie(f"{COOKIE}=x; {COOKIE}={token}"), key)
    assert caller.via == "device" and used == token
    assert _cookie_caller(app.extensions["puffpai"], parse_cookie(f"{COOKIE}=x"), key) == (None, None)
    assert _cookie_caller(app.extensions["puffpai"], parse_cookie(f"{COOKIE}={token}"), None) == (None, None)
    # A package token planted as the cookie never signs in either.
    pkg = app.extensions["puffpai"].tokens.issue_package_token("someapp")
    assert _raw(port, "/v1/settings", host, headers={"Cookie": f"{COOKIE}={pkg}"})[0] == 401


# -- 2b. the port-bound half: a stolen cookie answers nothing ------------------------------------------------
def test_a_stolen_cookie_with_every_forgeable_header_cannot_act_as_her(core, fake_app):
    """An app sends her browser to puffpai.localhost:<its own port>: cookies are not split by port, so its
    server gets her cookie. localStorage is split by port, so its page never gets the second half. With the
    cookie and every header it can forge, it still can't answer her one-tap Yes card or any owner route."""
    from puffpai_core.zone3.service import for_app
    app, port, _pp = core
    browser = Browser()
    origin, token = browser.desktop_sign_in(f"http://127.0.0.1:{port}", app.extensions["puffpai"].state)
    app_origin = f"http://puffpai.localhost:{fake_app.port}"
    assert browser.open(f"{app_origin}/")[0] == 200                       # the app's page, on the Desktop's name
    stolen = fake_app.seen[-1].get("cookie", "")
    assert f"{cookie_name(port)}={token}" in stolen                       # it really has the cookie
    assert DEVICE_KEY not in browser.storage.get(app_origin, {})          # never the second half
    assert token not in json.dumps(browser.storage)                       # and the key is not the token

    consents = for_app(app).consents
    card = consents.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    cid = card["consent_id"]
    answer = {"answer": "yes", "factor": "tap", "card_sha": card["card_sha"]}
    yes = json.dumps(answer).encode()
    host = f"puffpai.localhost:{port}"
    forged = {"Cookie": stolen, "Origin": origin, "Referer": f"{origin}/", "X-PuffPAI": "1",
              "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Content-Type": "application/json"}
    tries = [forged, {**forged, "Authorization": f"Bearer {token}"}, {**forged, "X-PuffPAI-Key": token},
             {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}]
    for guess in ("", token, "ppb_" + "A" * 43, "1"):
        tries.append({**forged, DEVICE_HEADER: guess})
        tries.append({**forged, "Authorization": f"Bearer {token}", DEVICE_HEADER: guess})
    for headers in tries:
        for path, body in ((f"/v1/consent/{cid}/answer", yes), ("/v1/vault/unlock", b"{}")):
            status = _raw(port, path, host, "POST", headers, body)[0]
            assert status in (401, 403), (path, sorted(headers), status)
        assert _raw(port, "/v1/settings", host, "GET", headers)[0] in (401, 403), sorted(headers)
    assert consents.get(cid)["status"] == "pending"                       # her card is untouched

    # The real Desktop, with the second half from its own localStorage, answers it.
    status, _, body = browser.page_call(origin, f"/v1/consent/{cid}/answer", "POST", answer)
    assert status == 200, body
    assert consents.get(cid)["status"] == "granted"


def test_a_cookie_without_its_second_half_is_no_cookie(core):
    app, port, _pp = core
    tokens = app.extensions["puffpai"].tokens
    host = f"puffpai.localhost:{port}"
    old = tokens.issue_device("browser paired before D79")               # never bound: it must pair again
    assert _raw(port, "/v1/settings", host, headers={"Cookie": f"{COOKIE}={old}"})[0] == 401
    token = tokens.issue_device("browser")
    key = tokens.bind_device(token)
    assert _raw(port, "/v1/settings", host, headers={"Cookie": f"{COOKIE}={token}"})[0] == 401
    assert _raw(port, "/v1/settings", host, headers={"Cookie": f"{COOKIE}={token}", DEVICE_HEADER: key})[0] == 200
    other = tokens.bind_device(tokens.issue_device("her other browser"))  # another device's half: no
    assert _raw(port, "/v1/settings", host, headers={"Cookie": f"{COOKIE}={token}", DEVICE_HEADER: other})[0] == 401
    # A browser's login from before D79 (no second half) is refused everywhere: by cookie, as a bearer and as
    # X-PuffPAI-Key, on 127.0.0.1 and on the Desktop's name.
    old_browser = tokens.issue_device("paired browser: Edge before D79")
    for h in (f"127.0.0.1:{port}", host):
        assert _raw(port, "/v1/settings", h, headers={"Authorization": f"Bearer {old_browser}"})[0] == 401
        assert _raw(port, "/v1/settings", h, headers={"X-PuffPAI-Key": old_browser})[0] == 401
    # The terminal's key (a program, never a browser) still works as a bearer, unbound.
    core_ = app.extensions["puffpai"]
    from puffpai_core.api.app import TERMINAL_LABEL
    core_.terminal_label = f"{TERMINAL_LABEL} (run test)"
    cli = tokens.issue_device(core_.terminal_label)
    assert _raw(port, "/v1/settings", f"127.0.0.1:{port}", headers={"Authorization": f"Bearer {cli}"})[0] == 200
    assert tokens.bind_device(tokens.issue_package_token("someapp")) is None


def test_an_old_browser_token_gets_no_owner_device_rights(core):
    """The verifier's repro: a browser token from before D79 still sits in old browsers as a 127.0.0.1 cookie,
    which reaches every app's port. As a bearer (or X-PuffPAI-Key) it must get nothing, not the Developer
    shell, not her Yes, not her knowledge sources."""
    from puffpai_core.zone3.service import for_app
    app, port, _pp = core
    tokens = app.extensions["puffpai"].tokens
    old = tokens.issue_device("paired browser: Mozilla/5.0 (Windows NT 10.0)")
    consents = for_app(app).consents
    card = consents.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    yes = json.dumps({"answer": "yes", "factor": "tap", "card_sha": card["card_sha"]}).encode()
    js = {"Content-Type": "application/json"}
    for cred in ({"Authorization": f"Bearer {old}"}, {"X-PuffPAI-Key": old}):
        for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"puffpai.localhost:{port}"):
            h = {**cred, **js}
            assert _raw(port, "/v1/dev/shell", host, "POST", h, b'{"shell": "powershell"}')[0] == 401
            assert _raw(port, f"/v1/consent/{card['consent_id']}/answer", host, "POST", h, yes)[0] == 401
            assert _raw(port, "/v1/knowledge/sources", host, "GET", cred)[0] == 401
    assert consents.get(card["consent_id"])["status"] == "pending"


def test_old_browser_tokens_are_revoked_at_core_start_and_on_upgrade(tmp_path):
    import sqlite3
    from puffpai_core.api import auth
    from puffpai_core.api.app import start_core, stop_core
    # On upgrade: a tokens.db from before D79 (no bind_hash column) loses its browser logins, nothing else.
    state = tmp_path / "upgrade"
    state.mkdir()
    db = sqlite3.connect(state / "tokens.db")
    db.execute("CREATE TABLE tokens (hash TEXT PRIMARY KEY, via TEXT NOT NULL, package_id TEXT,"
               " label TEXT NOT NULL, created_at TEXT NOT NULL)")
    rows = [("ppd_browser", "device", "paired browser: Edge"), ("ppd_cli", "device", "a program"),
            ("ppk_owner", "api_key", "owner key")]
    for token, via, label in rows:
        db.execute("INSERT INTO tokens VALUES (?, ?, NULL, ?, '2026-10-01')", (auth._hash(token), via, label))
    db.commit()
    db.close()
    store = auth.TokenStore(state / "tokens.db")
    assert store.lookup("ppd_browser") is None
    assert store.lookup("ppd_cli").via == "device" and store.lookup("ppk_owner").via == "api_key"

    # At every Core start: an unbound browser login (one that slipped in after the upgrade) is revoked; a
    # paired one with its second half stays, and so do other programs' keys.
    app = create_app(tmp_path / "state", port=_free_port(), pages_port=_free_port())
    core_ = app.extensions["puffpai"]
    core_.proxy_port = 0                           # a free port, never the real 8421
    old = core_.tokens.issue_device("paired browser: Edge before D79")
    paired = core_.tokens.issue_device("paired browser: Edge")
    key = core_.tokens.bind_device(paired)
    other = core_.tokens.issue_device("a program")
    terminal = start_core(app)
    try:
        assert core_.tokens.lookup(old) is None
        assert core_.tokens.bound_ok(paired, key)
        assert core_.tokens.lookup(other) is not None
    finally:
        stop_core(core_, terminal)
        core_.steward.stop_all(remember=False)


# -- 3. the Host check, the old address, the reserved id ---------------------------------------------------
def test_host_check_refuses_other_names(core):
    app, port, pp = core
    for host in (f"puffpai.localhost:{port}", f"127.0.0.1:{port}", f"localhost:{port}"):
        assert _raw(port, "/v1/health", host)[0] == 200, host
    for host in (f"evil.example:{port}", f"puffpai.localhost:{port + 1}", "puffpai.localhost", f"x.localhost:{port}",
                 f"127.0.0.2:{port}", ""):
        assert _raw(port, "/v1/health", host)[0] == 421, host
    assert _raw(pp, "/v1/health", f"127.0.0.1:{pp}")[0] == 200
    assert _raw(pp, "/v1/health", f"evil.example:{pp}")[0] == 421


def test_old_address_sends_her_on_and_never_takes_the_cookie(core):
    app, port, _pp = core
    core_ = app.extensions["puffpai"]
    token = core_.tokens.issue_device("browser")
    for path in ("/", "/ui/logic.js", "/v1/recover", "/?a=1"):
        status, headers, set_cookie, body = _raw(port, path, f"127.0.0.1:{port}",
                                                 headers={"Cookie": f"{COOKIE}={token}"})
        assert status == 302, path
        assert headers["location"] == f"http://puffpai.localhost:{port}{path}"
        assert not set_cookie
        assert b"puffpai.localhost" in body
    # The cookie is not read on the old address; a bearer still works there (the CLI, Toolsmith).
    assert _raw(port, "/v1/settings", f"127.0.0.1:{port}", headers={"Cookie": f"{COOKIE}={token}"})[0] == 401
    assert _raw(port, "/v1/settings", f"localhost:{port}", headers={"Cookie": f"{COOKIE}={token}"})[0] == 401
    assert _raw(port, "/v1/settings", f"127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"})[0] == 200
    bound = core_.tokens.issue_device("a paired browser")
    bound_key = core_.tokens.bind_device(bound)                          # a browser's token as a bearer: only
    assert _raw(port, "/v1/settings", f"127.0.0.1:{port}", headers={"Authorization": f"Bearer {bound}"})[0] == 401
    assert _raw(port, "/v1/settings", f"127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {bound}", DEVICE_HEADER: bound_key})[0] == 200
    # Pairing is refused there, and the code is not used up.
    code = pairing.new_code(core_.state)
    body = json.dumps({"code": code}).encode()
    js = {"Content-Type": "application/json"}
    status, _h, set_cookie, _b = _raw(port, "/v1/pair", f"127.0.0.1:{port}", "POST", js, body)
    assert status == 403 and not set_cookie
    status, _h, set_cookie, _b = _raw(port, "/v1/pair", f"puffpai.localhost:{port}", "POST", js, body)
    assert status == 200 and len(set_cookie) == 1
    reply = json.loads(_b)
    assert reply["device_key"].startswith("ppb_") and reply["device_key"] not in set_cookie[0]
    assert _h.get("cache-control") == "no-store"
    cookie = set_cookie[0]
    assert cookie.startswith(f"{cookie_name(port)}=ppd_") and "domain" not in cookie.lower() and "HttpOnly" in cookie


def test_puffpai_is_a_reserved_package_id(core):
    _app, _port, pp = core
    good = {"schema": "puffpai-package/1", "id": "puffpai", "kind": "page", "name": "X", "version": "1.0.0",
            "description": "x", "author": "x", "license": "MIT", "consent": "x"}
    assert manifest_mod.RESERVED_ID_LINE in manifest_mod.validate(good)
    assert manifest_mod.RESERVED_ID_LINE not in manifest_mod.validate({**good, "id": "puffpai-2"})
    assert _raw(pp, "/", f"puffpai.localhost:{pp}")[0] == 404           # never a package page's origin
