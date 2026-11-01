"""D129: several bots on one computer, one browser signed in to all of them.

Cookies are not split by port, so with one shared name each bot's sign-in replaced the last one's. Each Core now sets
``puffpai_device_<port>`` (the plain ``puffpai_device`` stays on the usual port only). It reads its own name and the plain
one, and every value goes through the same ``_cookie_caller`` checks. D79's two halves don't change: every bot is still
sent every other bot's cookie, so a bot on this computer can see another bot's cookie value. That is why the second half
exists, in localStorage, which is split by port.

Real listeners on free ports (never 8420-8422) and a real cookie jar as the browser, from ``test_d79_desktop_host``.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from types import SimpleNamespace

import pytest
from werkzeug.http import parse_cookie

from conftest import HELLO, manifest_sha
from desktop_kit import desktop_client
from puffpai_core import pairing
from puffpai_core.api import auth
from puffpai_core.api.app import _cookie_caller, create_app, make_servers, run_servers
from puffpai_core.api.auth import MAX_COOKIES, cookie_name, cookie_tokens
from puffpai_core.registry import row_from_manifest
from puffpai_core.steward.steward import Running
from test_d79_desktop_host import DEVICE_HEADER, DEVICE_KEY, Browser, _free_port, _raw

PLAIN = "puffpai_device"


@pytest.fixture
def bot(tmp_path):
    """Makes real bots: each with its own state folder and its own ports, like ``puffpai serve``."""
    running = []

    def make(name):
        port, pages = _free_port(), _free_port()
        app = create_app(tmp_path / name, port=port, pages_port=pages)
        stop = threading.Event()
        t = threading.Thread(target=run_servers, args=(make_servers(app, port, pages),), kwargs={"stop": stop}, daemon=True)
        t.start()
        running.append((stop, t))
        core = app.extensions["puffpai"]
        return SimpleNamespace(app=app, core=core, tokens=core.tokens, state=core.state, port=port, pages=pages,
                               host=f"puffpai.localhost:{port}", origin=f"http://puffpai.localhost:{port}",
                               cookie=cookie_name(port))

    yield make
    for stop, t in running:
        stop.set()
        t.join(timeout=10)


def sign_in(browser, b):
    """What the Desktop does with a pairing link. Returns (token, second half)."""
    code = pairing.new_code(b.state)
    status, _, body = browser.open(f"{b.origin}/v1/pair", "POST", {"code": code}, {"Origin": b.origin, "X-PuffPAI": "1"})
    assert status == 200
    key = json.loads(body)["device_key"]
    browser.storage.setdefault(b.origin, {})[DEVICE_KEY] = key    # desktop.js: localStorage.setItem
    return next(c.value for c in browser.jar if c.name == b.cookie), key


def settings(browser, b):
    return browser.page_call(b.origin, "/v1/settings")[0]


def paired(b):
    token = b.tokens.issue_device(f"{auth.BROWSER_LABEL}test")
    return token, b.tokens.bind_device(token)


def call(b, cookie=None, half=None, path="/v1/settings", method="GET", extra=None, body=None):
    headers = dict(extra or {})
    if cookie:
        headers["Cookie"] = cookie
    if half:
        headers[DEVICE_HEADER] = half
    return _raw(b.port, path, b.host, method, headers, body)


# -- the name ----------------------------------------------------------------------------------------------
def test_each_port_has_its_own_name_and_the_usual_port_keeps_the_plain_one():
    assert cookie_name(8420) == PLAIN
    assert cookie_name(8430) == f"{PLAIN}_8430"
    assert cookie_name(8440) != cookie_name(8430)


def test_a_first_bot_on_the_usual_port_is_not_signed_out(state):
    """Nothing is bound: a test client at the usual port's Desktop address."""
    app = create_app(state)                                 # 8420, the default
    core = app.extensions["puffpai"]
    client = desktop_client(app)
    code = pairing.new_code(state)
    r = client.post("/v1/pair", json={"code": code})
    assert r.status_code == 200 and r.headers["Set-Cookie"].startswith(f"{PLAIN}=ppd_")
    client.device_key = r.json["device_key"]
    assert client.get("/v1/settings").status_code == 200
    # A sign-in from before this, a plain cookie with its second half, still counts. A name it never set does not.
    token = core.tokens.issue_device("browser")
    key = core.tokens.bind_device(token)
    assert _cookie_caller(core, parse_cookie(f"{PLAIN}={token}"), key)[1] == token
    assert _cookie_caller(core, parse_cookie(f"{PLAIN}_8420={token}"), key) == (None, None)
    assert _cookie_caller(core, parse_cookie(f"{PLAIN}_8430={token}"), key) == (None, None)


# -- the clash itself ------------------------------------------------------------------------------------------
def test_one_browser_stays_signed_in_to_any_number_of_bots(bot):
    bots = [bot(f"bot-{n}") for n in range(4)]
    browser = Browser()
    tokens = [sign_in(browser, b)[0] for b in bots]
    assert len(set(tokens)) == 4
    assert {c.name: c.value for c in browser.jar} == {b.cookie: t for b, t in zip(bots, tokens)}
    for b in bots + bots[::-1]:                              # back to each, in either order
        assert settings(browser, b) == 200, b.port


def test_a_device_removed_on_one_bot_leaves_the_others_alone(bot):
    a, b, c = bot("bot-a"), bot("bot-b"), bot("bot-c")
    browser = Browser()
    tokens = {x.port: sign_in(browser, x)[0] for x in (a, b, c)}
    assert b.tokens.revoke(tokens[b.port])                   # no route does this yet; the store's own step
    assert (settings(browser, a), settings(browser, b), settings(browser, c)) == (200, 401, 200)
    sign_in(browser, b)                                      # signing in again replaces only b's own
    assert (settings(browser, a), settings(browser, b), settings(browser, c)) == (200, 200, 200)
    assert next(x.value for x in browser.jar if x.name == a.cookie) == tokens[a.port]


# -- what another bot can do with a cookie it is sent ------------------------------------------------------------
def test_another_bots_cookie_counts_for_nothing(bot):
    a, b = bot("bot-a"), bot("bot-b")
    browser = Browser()
    ta, ka = sign_in(browser, a)
    tb, kb = sign_in(browser, b)
    # The browser really does send a's cookie to b: cookies are not split by port.
    sent = {}

    class See(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            sent["cookie"] = self.headers.get("Cookie", "")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), See)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert browser.open(f"http://puffpai.localhost:{srv.server_address[1]}/")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
    assert f"{a.cookie}={ta}" in sent["cookie"] and f"{b.cookie}={tb}" in sent["cookie"]
    assert ka not in sent["cookie"] and kb not in sent["cookie"]
    # It is not in b's store, and the second half does not match.
    assert b.tokens.lookup(ta) is None and a.tokens.lookup(tb) is None
    assert call(b, f"{b.cookie}={tb}", kb)[0] == 200         # the control
    for cookie, half in ((f"{a.cookie}={ta}", None), (f"{a.cookie}={ta}", ka), (f"{PLAIN}={ta}", ka), (f"{PLAIN}={ta}", None),
                         (f"{b.cookie}={ta}", ka), (f"{b.cookie}={ta}", kb), (f"{b.cookie}={tb}", ka), (f"{b.cookie}={tb}", None)):
        assert call(b, cookie, half)[0] == 401, (cookie.split("=")[0], half == ka)
    # And the same, what b could try back at a, with what it was sent: a's cookie with no second half.
    assert call(a, f"{a.cookie}={ta}")[0] == 401
    assert call(a, f"{a.cookie}={ta}", kb)[0] == 401
    assert call(a, f"{a.cookie}={ta}", ka)[0] == 200


def test_another_bots_name_is_not_read_even_with_a_good_token(bot):
    a = bot("bot-a")
    token, key = paired(a)
    assert call(a, f"{a.cookie}={token}", key)[0] == 200
    for name in (cookie_name(a.port + 1), f"{a.cookie}x", f"{PLAIN}_0{a.port}", a.cookie.upper(), f"{PLAIN}_", f"x{a.cookie}"):
        assert call(a, f"{name}={token}", key)[0] == 401, name


def test_a_plain_sign_in_from_before_still_works_on_another_port(bot):
    b = bot("bot-b")
    token, key = paired(b)
    assert call(b, f"{PLAIN}={token}", key)[0] == 200
    assert call(b, f"{PLAIN}={token}")[0] == 401             # still both halves
    assert call(b, f"{b.cookie}=x; {PLAIN}={token}", key)[0] == 200


# -- planted cookies -----------------------------------------------------------------------------------------
def test_planted_cookies_never_sign_in_or_hide_the_real_one(bot):
    a = bot("bot-a")
    token, key = paired(a)
    own, pkg = a.cookie, a.tokens.issue_package_token("someapp")
    for cookie, want in ((f"{own}=x; {own}={token}", 200), (f"{own}={token}; {own}=x", 200), (f"{PLAIN}=x; {own}={token}", 200),
                         (f"{own}=x; {PLAIN}={token}", 200), (f"{own}=x; {own}=y", 401), (f"{own}=x", 401), (f"{PLAIN}=x", 401),
                         (f"{own}={pkg}", 401), (f"{PLAIN}={pkg}", 401)):
        assert call(a, cookie, key)[0] == want, cookie.replace(token, "<real>").replace(pkg, "<package>")
    # The login that counts (a Developer shell belongs to it) is the real token, not the planted first one.
    caller, used = _cookie_caller(a.core, parse_cookie(f"{own}=x; {PLAIN}=y; {own}={token}"), key)
    assert caller.via == "device" and used == token


def test_planted_for_real_in_the_cookie_jar(bot):
    """Another server on the same name plants under this bot's own name, on a longer path (sent first), and with
    ``Domain=localhost`` (the jar accepts it, the worst case). She stays signed in."""
    a = bot("bot-a")
    browser = Browser()
    token, _key = sign_in(browser, a)

    class Plant(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Set-Cookie", f"{a.cookie}=x; Path=/v1")
            self.send_header("Set-Cookie", f"{a.cookie}=y; Domain=localhost; Path=/v1/settings")
            self.send_header("Set-Cookie", f"{PLAIN}=z; Path=/v1")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Plant)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert browser.open(f"http://puffpai.localhost:{srv.server_address[1]}/")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()
    assert {c.value for c in browser.jar if c.name == a.cookie} == {"x", "y", token}
    assert settings(browser, a) == 200


def test_the_limit_still_bounds_the_lookups_and_the_plain_name_cant_use_it_up(bot):
    a = bot("bot-a")
    token, key = paired(a)
    plain = "; ".join(f"{PLAIN}=p{n}" for n in range(MAX_COOKIES + 4))
    # A flood under the plain name comes second: the real one is still looked at.
    assert call(a, f"{plain}; {a.cookie}={token}", key)[0] == 200
    own = "; ".join(f"{a.cookie}=o{n}" for n in range(MAX_COOKIES + 4))
    got = cookie_tokens(parse_cookie(f"{plain}; {own}"), a.port)
    assert len(got) == MAX_COOKIES and got == [f"o{n}" for n in range(MAX_COOKIES)]
    seen = []
    lookup = a.tokens.lookup
    a.tokens.lookup = lambda t: (seen.append(t), lookup(t))[1]
    assert _cookie_caller(a.core, parse_cookie(f"{plain}; {own}"), key) == (None, None)
    assert len(seen) == MAX_COOKIES
    # Neither name repeats a value, and on the usual port the two names are one.
    assert cookie_tokens(parse_cookie(f"{a.cookie}=v; {PLAIN}=v"), a.port) == ["v"]
    assert cookie_tokens(parse_cookie(f"{PLAIN}=v; {PLAIN}=w"), 8420) == ["v", "w"]
    assert cookie_tokens({a.cookie: "only"}, a.port) == ["only"]  # a plain dict of cookies too


# -- where the cookie must never go ------------------------------------------------------------------------------
def test_old_addresses_and_the_pages_listener_never_read_or_set_it(bot):
    a = bot("bot-a")
    token, key = paired(a)
    both = {"Cookie": f"{a.cookie}={token}; {PLAIN}={token}", DEVICE_HEADER: key}
    for port, host in ((a.pages, f"127.0.0.1:{a.pages}"), (a.pages, f"localhost:{a.pages}"), (a.pages, f"nopackage.localhost:{a.pages}"),
                       (a.port, f"127.0.0.1:{a.port}"), (a.port, f"localhost:{a.port}")):
        status, _h, set_cookie, _b = _raw(port, "/v1/settings", host, headers=both)
        assert status in (401, 403, 404) and not set_cookie, host
    # Pairing there sets nothing and does not use up the code.
    code = pairing.new_code(a.state)
    body = json.dumps({"code": code}).encode()
    js = {"Content-Type": "application/json"}
    for port, host in ((a.port, f"127.0.0.1:{a.port}"), (a.port, f"localhost:{a.port}"), (a.pages, f"127.0.0.1:{a.pages}"),
                       (a.pages, f"nopackage.localhost:{a.pages}")):
        status, _h, set_cookie, _b = _raw(port, "/v1/pair", host, "POST", js, body)
        assert status != 200 and not set_cookie, host
    assert _raw(a.port, "/v1/pair", a.host, "POST", js, body)[0] == 200


def test_a_package_page_never_gets_or_plants_it(bot, tmp_path):
    """A package page's own origin, forwarded for real: the new names go the same way as the old one."""
    import shutil
    a = bot("bot-a")
    token, key = paired(a)
    seen = []

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("Set-Cookie", f"{a.cookie}=planted; Path=/")
            self.send_header("Set-Cookie", f"{PLAIN}=planted; Path=/")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    folder = tmp_path / "pkg-a"
    shutil.copytree(HELLO, folder)
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    manifest["id"] = "pkg-a"
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    a.core.installs.set("pkg-a", folder, link=True, source=str(folder))
    a.core.registry.add(row_from_manifest(manifest, "made", manifest_sha=manifest_sha(folder)))
    a.core.steward._running["pkg-a"] = Running("pkg-a", "command", srv.server_address[1], "ppt_pkg-a", time.time())
    try:
        status, _h, set_cookie, body = _raw(a.pages, "/", f"pkg-a.localhost:{a.pages}",
                                            headers={"Cookie": f"{a.cookie}={token}; {PLAIN}={token}; other=1", DEVICE_HEADER: key})
        assert status == 200 and body == b"ok" and not set_cookie
        assert len(seen) == 1 and "cookie" not in seen[0] and token not in json.dumps(seen[0]) and key not in json.dumps(seen[0])
    finally:
        a.core.steward._running.pop("pkg-a", None)
        srv.shutdown()
        srv.server_close()


# -- the pair route and /v1/signin -----------------------------------------------------------------------------
def test_the_pair_route_sets_only_its_own_name_with_the_same_attributes(bot):
    a = bot("bot-a")
    body = json.dumps({"code": pairing.new_code(a.state)}).encode()
    status, headers, set_cookie, reply = _raw(a.port, "/v1/pair", a.host, "POST", {"Content-Type": "application/json"}, body)
    assert status == 200 and len(set_cookie) == 1 and headers["cache-control"] == "no-store"
    first, *attrs = [p.strip() for p in set_cookie[0].split(";")]
    name, _, value = first.partition("=")
    assert name == a.cookie and name == f"{PLAIN}_{a.port}" and value.startswith("ppd_")
    assert json.loads(reply)["device_key"] not in set_cookie[0]
    named = {p.split("=", 1)[0].lower(): p.split("=", 1)[-1] for p in attrs}
    assert named["path"] == "/" and named["samesite"] == "Strict" and named["max-age"] == str(400 * 24 * 3600)
    assert "httponly" in named and "domain" not in named and "secure" not in named


def test_signin_says_signed_in_only_for_this_bots_own_login(bot):
    a, b = bot("bot-a"), bot("bot-b")
    ta, ka = paired(a)
    tb, kb = paired(b)
    assert "cli" in json.loads(call(b, f"{b.cookie}={tb}", kb, "/v1/signin")[3])
    for cookie, half in ((f"{a.cookie}={ta}", ka), (f"{b.cookie}={ta}", ka), (f"{b.cookie}={tb}", None), (None, None)):
        assert "cli" not in json.loads(call(b, cookie, half, "/v1/signin")[3])


def test_a_state_change_needs_this_bots_own_origin(bot):
    a, b = bot("bot-a"), bot("bot-b")
    token, key = paired(a)
    body = json.dumps({"id": "desktop"}).encode()
    js = {"Content-Type": "application/json"}
    good = {**js, "Origin": a.origin, "X-PuffPAI": "1"}
    assert call(a, f"{a.cookie}={token}", key, "/v1/settings/ui", "PUT", good, body)[0] not in (401, 403)
    for headers in ({**js, "Origin": b.origin, "X-PuffPAI": "1"}, {**js, "Origin": a.origin}, {**js, "X-PuffPAI": "1"}):
        assert call(a, f"{a.cookie}={token}", key, "/v1/settings/ui", "PUT", headers, body)[0] == 403, sorted(headers)
