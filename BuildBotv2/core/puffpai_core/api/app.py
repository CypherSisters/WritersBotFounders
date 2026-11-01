"""The Core API app: ``create_app(...)`` and ``serve(...)`` (waitress on 127.0.0.1, and on the home network unless she turns it off, D130).

Every request passes one gate before its view: the route's declared access
level (``routing.route`` / ``routing.stub``) against the caller found by
``auth.credential`` and the token store. A route without a declared level is
refused, so a new route can't skip auth by mistake.

Two addresses, one app, one process (D49):
- the Core's own address (port 8420): the Desktop and the Core API. The Desktop
  is served, and its paired-device cookie set and read, only when ``Host`` is
  exactly ``puffpai.localhost:<port>`` (D79): a cookie is not split by port, so
  a cookie on 127.0.0.1 would reach every app on 127.0.0.1. Programs (the CLI,
  the wizard, Toolsmith, packages) keep calling ``127.0.0.1:<port>`` with their
  bearer tokens; nothing in Python needs ``puffpai.localhost`` to resolve. On
  ``127.0.0.1`` or ``localhost`` the Desktop's pages only answer a small page
  that sends the browser to ``http://puffpai.localhost:<port>/`` (no cookie
  set or read there), and ``/v1/pair`` is refused. Any other ``Host`` is
  refused on both listeners (421, DNS rebinding). A cookie-signed state change
  must come from the Desktop's own page (``auth.cookie_request_ok``). Package
  pages (``/pkg/``) are never served here, so a package page can never run
  with the Core's origin.
- the pages address (127.0.0.1:8422): package pages under ``/pkg/`` (lane L1
  forwards them) and the Core API routes a package token may call. The
  device cookie is never read here, and owner credentials are refused, so a
  page has only its own package's rights.
``serve`` runs one waitress listener per address; each stamps
``environ["puffpai.listener"]`` with ``core`` or ``pages`` before the app sees
the request. The stamp is set by the server, never from a request header.
A request with no stamp (the Flask test client, another WSGI host) is treated
as the Core's own address.

Every package page has its own origin (D53): ``http://<id>.localhost:8422``,
on the same pages listener. A request there whose ``Host`` is
``<id>.localhost:<pages port>`` (a valid package id) is routed to that
package (``environ["puffpai.page_package"]``, path ``/pkg/<id>/<path>``
inside the app) and nothing else, so a package origin never reaches the Core
API. Any other ``*.localhost`` host on the pages listener gets 404 (the id
``puffpai`` is reserved for the Desktop, D79, so it is never a page). On
``127.0.0.1:8422`` itself, ``/pkg/<id>/<path>`` only redirects (302, GET) to
the package's own origin (``groups.pages``).

The home network (D130, ``api.network``; on unless she turns it off, and no change to the two above): one more listener
for the Desktop and the API, and one page listener per package, each with its own stamp set by the server. Only a
paired browser's two halves count there (``_network_gate``, ``_network_page_gate``).

Core start and stop: ``create_app`` builds the app only (no Zone 3, no
wiring, nothing started). ``start_core(app)`` is what ``serve`` calls: it makes
Zone 3 and joins the lanes (``wire_core``: ``start_zone3`` + ``wiring.wire``),
runs the self-test, expires Yes cards left from the last run and empties
staging (lane L1), starts the Bridge proxy and the bypass watch
(``wiring.start``), revokes every earlier terminal key and issues one bound to
this run (``settings\\terminal.json``), revokes every browser login with no
second half (D79), starts again what was running and
starts Caretaker. ``stop_core`` stops every package (D18), the proxy and the
watch, and ends the terminal key. Tests that need the wiring call
``start_core`` (or ``wire_core`` for the joins alone) themselves.

One Core per bot (D91): ``serve`` holds the bot's lock (``corelock``) from
before it binds anything until it has stopped, and its listeners bind so no
other program can share their ports (``make_servers``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import signal
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from flask import Flask, Response, g, jsonify, redirect, request

from ..bus import Bus
from ..caretaker import Caretaker
from ..consent import ConsentStore
from ..packages.installs import Installs
from ..packages.manager import PackageManager
from ..packages.manifest import RESERVED_IDS
from ..packages.providers import Providers
from ..paths import state_dir
from ..registry import Registry
from ..steward import Steward
from . import auth, network
from .groups import ALL

HOST = "127.0.0.1"
#: The Desktop's own name (D79). Browsers resolve every ``*.localhost`` to this computer by themselves; the
#: Core still listens on 127.0.0.1 only, and no Python caller ever needs this name to resolve.
DESKTOP_HOST = "puffpai.localhost"
PORT = 8420
PAGES_PORT = 8422
LISTENER = "puffpai.listener"
CORE_LISTENER = "core"
PAGES_LISTENER = "pages"
PAGE_PACKAGE = "puffpai.page_package"          # set by the app from Host, on the pages listener only (D53)
_PACKAGE_ID = re.compile(r"[a-z][a-z0-9-]{1,39}")
TERMINAL_LABEL = "this computer's terminal"

# Core API groups a page may reach on the pages address (with its package token), besides /pkg/.
_PAGES_OPEN = ("health",)


@dataclass
class Core:
    """The parts a route can reach through ``routing.current_core()``."""

    state: Path
    registry: Registry
    tokens: auth.TokenStore
    bus: Bus
    consent: ConsentStore | None = None       # lane L1: Yes cards
    installs: Installs | None = None          # lane L1: where each package's folder is
    steward: Steward | None = None            # lane L1: runs packages
    caretaker: Caretaker | None = None        # lane L1: health, restarts, safe mode
    packages: PackageManager | None = None    # lane L1: the one install path
    providers: Providers | None = None        # lane L1: which provider fills each slot
    port: int = PORT                          # the Core's own address
    pages_port: int = PAGES_PORT              # the pages address (D49)
    terminal_label: str | None = None         # the one terminal key this run honours (lane L1)
    proxy_port: int = 8421                    # the Bridge proxy (lane L4); 0 = any free port
    zone3: Any = None                         # lane L4: the Bridge, Drawbridge, the proxy (``wiring``)
    vault: Any = None                         # the wizard's parts (``wiring``): lane L3's Archivist
    models: Any = None                        # lane L2's model slot
    bridge: Any = None                        # lane L4's posture
    toolsmith: Any = None                     # lane L6 (not merged yet)
    notices: Any = None                       # lane L5's notices (Caretaker's cards go there)
    app: Any = None                           # the Flask app this Core serves (``start_core`` takes either)


def desktop_origin(port: int = PORT) -> str:
    """The Desktop's own origin (D79): ``http://puffpai.localhost:<port>``."""
    return f"http://{DESKTOP_HOST}:{port}"


def core_origins(port: int = PORT) -> set[str]:
    """The browser origins of the Core's own address: the Desktop's only (D79). The Desktop never runs on
    127.0.0.1 or localhost any more, so a page there never counts as the Core's own."""
    return {desktop_origin(port)}


def on_desktop_host(app: Flask, environ: dict) -> bool:
    """True only when the request's ``Host`` is exactly ``puffpai.localhost:<the Core's port>`` (D79).

    The raw header, never a forwarded one. Only here is the Desktop served and its cookie set or read."""
    if environ.get(LISTENER) == PAGES_LISTENER:
        return False
    return (environ.get("HTTP_HOST") or "").strip().lower() == app.config["PUFFPAI_DESKTOP_HOST"]


def pages_origin(port: int = PAGES_PORT) -> str:
    return f"http://{HOST}:{port}"


def pages_port_of(pages: str) -> int:
    """The port of the pages address (``http://127.0.0.1:8422`` -> 8422)."""
    tail = pages.rstrip("/").rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 80


def package_origin(package_id: str, pages: str) -> str:
    """A package page's own origin (D53): ``http://<id>.localhost:<pages port>``."""
    return f"http://{package_id}.localhost:{pages_port_of(pages)}"


def package_origins_wildcard(pages: str) -> str:
    return f"http://*.localhost:{pages_port_of(pages)}"


_WRONG_HOST = (b'{"error": "BuildBot answers only at its own addresses on this computer '
               b'(puffpai.localhost, 127.0.0.1 or localhost, on its own port)."}')


def _refuse(start_response, status: str, body: bytes, kind: str = "application/json") -> list[bytes]:
    start_response(status, [("Content-Type", kind), ("Content-Length", str(len(body))),
                            ("Cache-Control", "no-store")])
    return [body]


def _page_hosts(app: Flask, wsgi: Callable) -> Callable:
    """D53: on the pages listener, ``Host: <id>.localhost:<pages port>`` means that package's page.

    D79: on a real listener (stamped by ``serve``), a ``Host`` that is not this computer's own name and that
    listener's own port is refused (421) before any route runs, so a website whose name was pointed at
    127.0.0.1 (DNS rebinding) never reaches the Core. The Core's listener answers ``puffpai.localhost``,
    ``127.0.0.1`` and ``localhost``; the pages listener ``127.0.0.1``, ``localhost`` and ``<id>.localhost``.
    A request with no stamp (a test client) is not checked here; the Desktop's cookie still needs
    ``Host: puffpai.localhost:<port>`` (``on_desktop_host``)."""
    def middleware(environ, start_response):
        environ[PAGE_PACKAGE] = None
        listener = environ.get(LISTENER)
        if listener in (network.NETWORK_LISTENER, network.NETWORK_PAGE_LISTENER):      # D130: before any route
            refused = app.extensions[network.EXTENSION].refuse(environ)
            if refused:
                return _refuse(start_response, refused[0], refused[1], network.REFUSAL_TYPE)
            if listener == network.NETWORK_PAGE_LISTENER:     # one package, from the socket, never from the request
                if not (environ.get("PATH_INFO") or "/").startswith("/"):    # never joined onto a package's id
                    return _refuse(start_response, "400 BAD REQUEST", b"That is not a page's address.", network.REFUSAL_TYPE)
                package = environ.get(network.NETWORK_PACKAGE)
                environ[PAGE_PACKAGE] = package
                environ["PATH_INFO"] = f"/pkg/{package}" + (environ.get("PATH_INFO") or "/")
            return wsgi(environ, start_response)
        host = (environ.get("HTTP_HOST") or "").strip().lower()
        name, sep, port = host.rpartition(":")
        if not sep:
            name, port = host, "80"                        # no port in Host means http's own, 80
        bound = str(environ.get("SERVER_PORT") or "")      # the listener's own port (waitress sets it from its socket)
        if listener == CORE_LISTENER and (name not in (DESKTOP_HOST, HOST, "localhost") or port != bound):
            return _refuse(start_response, "421 MISDIRECTED REQUEST", _WRONG_HOST)
        if listener == PAGES_LISTENER:
            want = str(pages_port_of(app.config["PUFFPAI_PAGES_ORIGIN"]))
            if name.endswith(".localhost"):
                id = name[: -len(".localhost")]
                if not _PACKAGE_ID.fullmatch(id) or id in RESERVED_IDS or port != want:
                    return _refuse(start_response, "404 NOT FOUND", b'{"error": "No such package page."}')
                environ[PAGE_PACKAGE] = id
                environ["PATH_INFO"] = f"/pkg/{id}" + (environ.get("PATH_INFO") or "/")
            elif name not in (HOST, "localhost") or port != bound:
                return _refuse(start_response, "421 MISDIRECTED REQUEST", _WRONG_HOST)
        return wsgi(environ, start_response)
    return middleware


def create_app(state: Path | str | None = None, registry: Registry | None = None,
               tokens: auth.TokenStore | None = None, bus: Bus | None = None,
               port: int = PORT, pages_port: int = PAGES_PORT,
               pin_check: Callable[[str], tuple[int, str] | None] | None = None) -> Flask:
    state = Path(state) if state else state_dir()
    state.mkdir(parents=True, exist_ok=True)
    app = Flask("puffpai_core")
    app.json.sort_keys = False
    app.config["PUFFPAI_CORE_ORIGINS"] = core_origins(port)
    app.config["PUFFPAI_DESKTOP_ORIGIN"] = desktop_origin(port)          # D79: where the Desktop and its cookie live
    app.config["PUFFPAI_DESKTOP_HOST"] = f"{DESKTOP_HOST}:{port}"
    app.config["PUFFPAI_PAGES_ORIGIN"] = pages_origin(pages_port)
    app.config["PUFFPAI_API_URL"] = f"http://{HOST}:{port}"   # where Toolsmith's own process calls (L6)
    registry = registry or Registry(state / "registry.db")
    tokens = tokens or auth.TokenStore(state / "tokens.db")
    bus = bus or Bus()
    consent = ConsentStore(state / "consent.db", bus)
    installs = Installs(state)
    steward = Steward(state, registry, tokens, installs, core_api=f"http://{HOST}:{port}", bus=bus)
    caretaker = Caretaker(registry, steward, bus)
    packages = PackageManager(state, registry, bus, consent, steward, installs, caretaker)
    app.extensions["puffpai"] = Core(state=state, registry=registry, tokens=tokens, bus=bus, consent=consent,
                                     installs=installs, steward=steward, caretaker=caretaker, packages=packages,
                                     providers=Providers(state, registry, consent, bus), port=port,
                                     pages_port=pages_port)
    # The PIN check for floor-level actions (D29). Lane L3 (the vault) installs it; until then
    # anything that needs the PIN is refused, never let through.
    app.extensions["puffpai.pin_check"] = pin_check
    app.extensions[network.EXTENSION] = network.Network(app, state, port)     # D130: off until she turns it on
    app.wsgi_app = _page_hosts(app, app.wsgi_app)      # D53: per-package page origins
    for group in ALL:
        app.register_blueprint(group.bp)
    for group in ALL:                         # a group that keeps parts of its own sets them up here
        init = getattr(group, "init_app", None)
        if callable(init):
            init(app)
    core = app.extensions["puffpai"]
    core.app = app
    core.notices = app.extensions.get("puffpai.notices")
    # No Zone 3 and no wiring here: ``start_core`` (what ``serve`` calls) does both (lane L6 agrees).

    @app.before_request
    def gate() -> Any:
        if request.endpoint is None:          # no such route: Flask answers 404/405
            if request.environ.get(LISTENER) == network.NETWORK_PAGE_LISTENER:
                return jsonify(error="No such page."), 404     # D130: never a redirect, which would name the package
            return None
        view = app.view_functions.get(request.endpoint)
        access = getattr(view, "puffpai_access", None)
        if access is None:
            return jsonify(error="This route declares no access level, so it is refused."), 403
        listener = request.environ.get(LISTENER)
        if listener == network.NETWORK_LISTENER:
            return _network_gate(app, access)
        if listener == network.NETWORK_PAGE_LISTENER:
            return _network_page_gate(app)
        if listener == PAGES_LISTENER:
            return _pages_gate(app, access)
        if request.blueprint == "pages":
            return jsonify(error="Package pages are served from the pages address "
                                 f"{app.config['PUFFPAI_PAGES_ORIGIN']}, never from the Core's own address."), 404
        desktop = on_desktop_host(app, request.environ)
        if request.endpoint == _PAIR_SCREEN:  # D130: the network's own; on this computer it is no route
            return jsonify(error="No such route. The routes are listed in contracts/openapi.yaml."), 404
        if request.endpoint in _DESKTOP_PAGES and not desktop:
            return moved_page(app)            # D79: the old address; no cookie is set or read here
        if request.endpoint == _PAIR and not desktop:
            return jsonify(error="Sign in at the Desktop's own address: "
                                 f"{app.config['PUFFPAI_DESKTOP_ORIGIN']}/."), 403
        token, _ = auth.credential(request.headers, request.cookies, allow_cookie=False)
        core = app.extensions["puffpai"]
        caller = core.tokens.lookup(token)
        second = request.headers.get(auth.DEVICE_HEADER, "").strip()
        if caller is not None and caller.needs_second_half and not core.tokens.bound_ok(token, second):
            caller = None                     # D79: a browser's token never counts without its second half
        from_cookie = False
        if token is None and desktop:         # D79: the cookie counts only at puffpai.localhost:<port>
            caller, token = _cookie_caller(core, request.cookies, second)
            from_cookie = caller is not None
        if caller is not None and caller.via == "device" and caller.label.startswith(TERMINAL_LABEL) \
                and caller.label != core.terminal_label:
            caller = None                     # a terminal key from another Core run (lane L1)
        if caller is not None and from_cookie and \
                not auth.cookie_request_ok(request.method, request.headers, app.config["PUFFPAI_CORE_ORIGINS"]):
            return jsonify(error=auth.CROSS_SITE), 403
        g.caller = caller
        g.token = token if caller is not None else None   # the one credential that signed in (D79)
        refused = auth.decide(access, caller, request.view_args or {})
        if refused:
            status, reason = refused
            return jsonify(auth.refusal(status, reason)), status
        return None

    @app.after_request
    def frame_rules(resp):
        pages = app.config["PUFFPAI_PAGES_ORIGIN"]
        listener = request.environ.get(LISTENER)
        if listener == network.NETWORK_LISTENER:
            # D130: the same rules as the Core's own address, naming the origins this device really uses.
            resp.headers["X-Frame-Options"] = "DENY"
            if "Content-Security-Policy" not in resp.headers:
                resp.headers["Content-Security-Policy"] = network_csp(  # the page ports, for a paired device only
                    app.extensions[network.EXTENSION].page_origins(request.host) if g.get("network_paired") else [])
            resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        elif listener == network.NETWORK_PAGE_LISTENER:
            from ..zone2.apps import puffpai_own
            own = request.environ.get(PAGE_PACKAGE)
            framed = (app.extensions[network.EXTENSION].desktop_origin(request.host)
                      if puffpai_own(app.extensions["puffpai"].registry.get(own)) else "'none'")
            resp.headers.add("Content-Security-Policy", package_csp(f"http://{request.host.lower()}", framed))
        elif listener == PAGES_LISTENER:
            # Added, never replacing a package's own policy (both hold). Pictures, fetches and forms stay
            # on this computer; only the Desktop may frame a package page.
            ancestors = " ".join(sorted(app.config["PUFFPAI_CORE_ORIGINS"]))
            own = request.environ.get(PAGE_PACKAGE)
            if own:                               # a package's own origin (D53): only itself
                # Only PuffPAI's own origin may be framed at all (D83, ``zone2.apps.puffpai_own``): the browser
                # holds this, whatever a page's own code decides. Third party: never in a frame, its own tab only.
                from ..zone2.apps import puffpai_own
                framed = ancestors if puffpai_own(app.extensions["puffpai"].registry.get(own)) else "'none'"
                resp.headers.add("Content-Security-Policy", package_csp(package_origin(own, pages), framed))
            else:
                resp.headers.add("Content-Security-Policy", pages_csp(pages, ancestors))
        else:
            # Nothing frames the Core's own pages (no click-tricks from a package page), and the
            # Desktop loads nothing from outside this computer (pictures included: they would skip the Bridge).
            resp.headers["X-Frame-Options"] = "DENY"
            if "Content-Security-Policy" not in resp.headers:     # the recovery page sets its own, stricter one
                resp.headers["Content-Security-Policy"] = core_csp(pages)
            resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(error="No such route. The routes are listed in contracts/openapi.yaml."), 404

    @app.errorhandler(405)
    def not_allowed(_e):
        return jsonify(error="That method is not allowed on this route."), 405

    return app


def start_zone3(app: Flask) -> Any:
    """Zone 3 (the Bridge, Drawbridge, the consent cards) for this Core, made once at Core start (``serve``, lane L4).

    Not in ``create_app``: making Zone 3 binds the vault (L3) and so the brain's gate, which a
    bare app (a test, a tool) must not get by accident; the routes make it on first use as before.

    Idempotent: L4's ``service.for_app`` keeps it in ``app.extensions["puffpai.zone3"]``. Without
    L4's code, or if it can't start, there is none, and every part that needs it answers
    "not running" (fail closed, X26): nothing leaves, nothing installs."""
    import importlib
    try:
        zone3_service = importlib.import_module("puffpai_core.zone3.service")
    except ImportError:
        return None
    for_app = getattr(zone3_service, "for_app", None)
    if not callable(for_app):
        return None
    try:
        return for_app(app)
    except Exception:  # noqa: BLE001 - a Zone 3 that can't start is no Zone 3 (closed)
        logging.getLogger(__name__).exception("Zone 3 did not start; the Bridge stays closed.")
        app.extensions.pop("puffpai.zone3", None)
        return None


def core_csp(pages: str) -> str:
    """The Core's own address: its files, plus pictures, frames and widget JSON from the package
    page origins (D53, ``http://*.localhost:8422``) and the pages address."""
    pkgs = package_origins_wildcard(pages)
    return (f"default-src 'self'; img-src 'self' {pages} {pkgs} data: blob:; frame-src {pkgs}; "
            f"connect-src 'self' {pages} {pkgs}; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'")


def network_csp(page_origins: list[str]) -> str:
    """The Desktop on the home network (D130): its own files, plus exactly its packages' page origins. No wildcard."""
    pkgs = " ".join(page_origins)
    frames = pkgs or "'none'"
    return (f"default-src 'self'; img-src 'self' {pkgs} data: blob:; frame-src {frames}; "
            f"connect-src 'self' {pkgs}; object-src 'none'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'")


def package_csp(own: str, ancestors: str) -> str:
    """A package page on its own origin (D53): it loads from, fetches from and posts to itself only.

    Inline script and style are allowed so ordinary package pages work; only the Desktop frames it."""
    here = f"'self' {own}"
    return (f"default-src {here}; script-src {here} 'unsafe-inline'; style-src {here} 'unsafe-inline'; "
            f"img-src {here} data: blob:; connect-src {here}; form-action {here}; base-uri 'self'; "
            f"object-src 'none'; frame-ancestors {ancestors}")


def pages_csp(pages: str, ancestors: str) -> str:
    """The pages address. ``'self'`` is also spelled out: a sandboxed frame has an opaque origin.

    Inline script and style are allowed so ordinary package pages work; nothing may load
    from, or post to, outside this computer.
    """
    here = f"'self' {pages}"
    return (f"default-src {here}; script-src {here} 'unsafe-inline'; style-src {here} 'unsafe-inline'; "
            f"img-src {here} data: blob:; connect-src {here}; form-action {here}; base-uri 'self'; "
            f"object-src 'none'; frame-ancestors {ancestors}")


def _pages_gate(app: Flask, access: str) -> Any:
    """The pages address: no owner login, ever; a package token's own rights only."""
    token, _ = auth.credential(request.headers, request.cookies, allow_cookie=False)
    caller = app.extensions["puffpai"].tokens.lookup(token)
    if caller is not None and caller.kind != "package":
        return jsonify(error="The pages address never takes the owner's login or keys. "
                             "Use the Core's own address for that."), 403
    g.caller = caller
    if request.blueprint != "pages" and request.blueprint not in _PAGES_OPEN \
            and access not in (auth.PACKAGE_OK, auth.OWN_ROW):
        return jsonify(error="That route is not on the pages address. A page can call its own package "
                             "and the Core API routes its package token allows."), 404
    refused = auth.decide(access, caller, request.view_args or {})
    if refused:
        status, reason = refused
        return jsonify(auth.refusal(status, reason)), status
    return None


def _network_gate(app: Flask, access: str) -> Any:
    """The network listener (D130). The cookie named for this port, with its second half, is the only credential: a
    program's key is refused here whatever it could do on 127.0.0.1. No route answers without a paired browser but the
    pair screen, pairing itself and where to pair. A paired browser then gets what the owner gets on this computer."""
    core: Core = app.extensions["puffpai"]
    net = app.extensions[network.EXTENSION]
    if request.blueprint == "pages":
        return jsonify(error="Package pages are on their own ports."), 404
    if request.headers.get("Authorization") or request.headers.get("X-PuffPAI-Key"):
        return jsonify(error=network.KEYS_REFUSED), 403
    name = network.cookie_name(net.port)
    caller, token = _cookie_caller(core, request.cookies, request.headers.get(auth.DEVICE_HEADER, "").strip(), name)
    g.caller, g.token, g.network_paired = caller, token, caller is not None
    if (caller is not None or request.endpoint == _PAIR) and not auth.cookie_request_ok(
            request.method, request.headers, {net.desktop_origin(request.host)}):
        return jsonify(error=auth.CROSS_SITE), 403
    if request.endpoint in (_PAIR, _PAIR_SCREEN, "ui.ui_signin"):
        return None
    if request.endpoint in ("ui.ui_root", "ui.ui_file", "ui.window_root", "ui.window_file", "ui.ui_recover"):
        # A page load can't send the second half, so the Desktop's files need the cookie of a paired browser. Without
        # one: the pair screen and nothing else. They hold no owner data either way; the API still needs both halves.
        if any(_paired_browser(core.tokens.lookup(v)) for v in auth.cookie_tokens(request.cookies, name)):
            g.network_paired = True
            return None
        if request.endpoint in ("ui.ui_root", "ui.window_root"):
            return redirect("/pair?back=window" if request.endpoint == "ui.window_root" else "/pair")   # D139: and back
    if caller is None:
        return jsonify(auth.refusal(401, auth.NO_CREDENTIAL)), 401
    refused = auth.decide(access, caller, request.view_args or {})
    if refused:
        status, reason = refused
        return jsonify(auth.refusal(status, reason)), status
    return None


def _network_page_gate(app: Flask) -> Any:
    """A package's page port (D130): that package's pass from a paired browser, or nothing reaches the package. A
    package token is refused here too. The Core adds the package's own token when it forwards (``groups.pages``)."""
    from .groups.pages import _say
    core: Core = app.extensions["puffpai"]
    net = app.extensions[network.EXTENSION]
    package = request.environ.get(PAGE_PACKAGE)
    g.caller = None
    if request.blueprint != "pages":
        return jsonify(error="No such route."), 404
    if request.headers.get("Authorization") or request.headers.get("X-PuffPAI-Key"):
        return jsonify(error=network.KEYS_REFUSED), 403
    port = str(request.environ.get("SERVER_PORT") or "")
    if not network.page_row_ok(core.registry.get(package)) or str(net.page_port(package)) != port:
        return _say(network.ON_COMPUTER, 404, False)
    passes = auth.cookie_tokens(request.cookies, network.page_cookie_name(int(port)))
    if not any(core.tokens.page_pass_ok(v, package) for v in passes):
        return _say("Open this from your Desktop, on a paired device.", 401, False)
    return None


#: The Desktop's own pages (D79): served only at ``puffpai.localhost:<port>``; anywhere else, ``moved_page``.
_DESKTOP_PAGES = frozenset({"ui.ui_root", "ui.ui_file", "ui.ui_recover", "ui.ui_signin",     # signin: D92
                            "ui.window_root", "ui.window_file"})                            # the window: D108
_PAIR = "ui.ui_pair"
_PAIR_SCREEN = "ui.ui_pair_screen"            # D130: the home network's pair screen


def _paired_browser(caller: auth.Caller | None) -> bool:
    return caller is not None and caller.via == "device" and caller.browser and caller.bound


def _cookie_caller(core: Core, cookies, second: str | None,
                   name: str | None = None) -> tuple[auth.Caller | None, str | None]:
    """The paired device that signs in by this Core's device cookie, or None (D79, D129).

    A browser may send several cookies with its names (a page on another ``*.localhost`` name can set one
    with ``Domain=localhost``, and the browser may send it first). Each one is checked against the token
    store, and the first that is a paired device of this Core run counts; a planted one never hides the
    real one, and never signs anyone in by itself. Returns that caller and its token, or (None, None).

    The cookie alone is never enough: its device must be bound, and *second* (``X-PuffPAI-Device``, from the
    Desktop's own localStorage) must be that binding's secret. A cookie without it is no cookie at all.

    *name*: the home network's cookie (D130), which only a paired browser's token counts for."""
    for value in auth.cookie_tokens(cookies, name or core.port):
        caller = core.tokens.lookup(value)
        if caller is None or caller.via != "device" or (name and not _paired_browser(caller)):
            continue                          # only a paired device signs in by cookie
        if not caller.bound or not core.tokens.bound_ok(value, second):
            continue                          # D79: the second half is missing or not this device's
        if caller.label.startswith(TERMINAL_LABEL) and caller.label != core.terminal_label:
            continue                          # a terminal key from another Core run (lane L1)
        return caller, value
    return None, None


MOVED = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>BuildBot has a new address</title>
<p>BuildBot's Desktop is now at <a href="{url}">{origin}</a>.</p>
<p>Open that link, and change any bookmark to it.</p>
</html>
"""


def moved_page(app: Flask) -> Any:
    """The Desktop's old address (``127.0.0.1:<port>`` or ``localhost:<port>``, D79): a small page that sends
    the browser to the same path at ``http://puffpai.localhost:<port>``. A 302, so a ``#pair=`` link keeps its
    code (the browser carries the ``#`` part over). It never sets a cookie and never reads one."""
    origin = app.config["PUFFPAI_DESKTOP_ORIGIN"]
    url = origin + quote(request.path, safe="/:@!$&'()*+,;=-._~")
    if request.query_string:
        url += "?" + quote(request.query_string.decode("latin-1"), safe="=&;/:@!$'()*+,-._~%")
    resp = Response(MOVED.format(url=escape(url), origin=escape(origin + "/")), status=302, mimetype="text/html")
    resp.headers["Location"] = url
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = ("default-src 'none'; base-uri 'none'; form-action 'none'; "
                                               "frame-ancestors 'none'")
    return resp


def stamp(app: Any, listener: str) -> Callable:
    """A WSGI wrapper that tells the app which address a request came in on."""
    def wsgi(environ, start_response):
        environ[LISTENER] = listener
        return app(environ, start_response)
    return wsgi


#: Workers on the Core address (D113). A worker stays taken for as long as its request runs, so these mostly wait:
#: each open page's event stream (up to 25 s, then again), a chat turn, and Toolsmith's own calls (an install runs to
#: its end inside one, up to 15 minutes). With 8, a few of those left no worker for the pages.
CORE_WORKERS = 32


def make_servers(app: Flask, port: int = PORT, pages_port: int = PAGES_PORT) -> list:
    """The two waitress listeners (Core address, pages address), not yet running. Each port is bound so no
    other program can share it (D91, ``corelock.exclusive_socket``): waitress alone sets ``SO_REUSEADDR``,
    which on Windows let a second Core bind the same ports while the first still listened. ``PortInUse`` (an
    OSError) when a port is in use."""
    from waitress import create_server

    from ..corelock import PortInUse, exclusive_socket
    # "Task queue depth is N" is not for her window (D113). Only that message uses this logger; real errors still
    # come through "waitress" as before.
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    try:
        core_sock = exclusive_socket(HOST, port)
    except OSError as exc:
        raise PortInUse(exc.errno, exc.strerror or str(exc)) from exc
    try:
        pages_sock = exclusive_socket(HOST, pages_port)
    except OSError as exc:
        core_sock.close()
        raise PortInUse(exc.errno, exc.strerror or str(exc)) from exc
    return [create_server(stamp(app, CORE_LISTENER), sockets=[core_sock], threads=CORE_WORKERS, ident="puffpai"),
            create_server(stamp(app, PAGES_LISTENER), sockets=[pages_sock], threads=4, ident="puffpai-pages")]


WIRED = "puffpai.wired"
STARTED = "puffpai.started"                   # the running Core's terminal key, while started


def _app_of(app_or_core: Any) -> Flask:
    app = app_or_core if isinstance(app_or_core, Flask) else getattr(app_or_core, "app", None)
    if not isinstance(app, Flask):
        raise ValueError("start_core needs the app (or a Core made by create_app)")
    return app


def wire_core(app_or_core: Any) -> Core:
    """Zone 3 for this app and the joins between the lanes (``wiring.wire``), once. Nothing is started:
    no proxy, no watch, no packages. ``start_core`` runs it first; an offline tool (the CLI with no
    Core running) runs it alone, so its installs get the Core's own Drawbridge and audit log."""
    app = _app_of(app_or_core)
    core: Core = app.extensions["puffpai"]
    if not app.extensions.get(WIRED):
        start_zone3(app)
        from .. import wiring
        core.notices = app.extensions.get("puffpai.notices")
        wiring.wire(app)                      # integration lane I1: the joins between the lanes
        app.extensions[WIRED] = True
    return core


def start_core(app_or_core: Any) -> str:
    """THE Core start (D57), called by ``serve`` only (tests call it too): ``wire_core`` (Zone 3 and the
    joins, once), lane L1's start, the Bridge proxy and the bypass watch (``wiring.start``), the terminal
    key, the saved packages, Caretaker, and lane L6's Toolsmith host. Takes the app (or the Core
    ``create_app`` made). Returns the terminal key. Idempotent: a second call while the Core runs starts
    nothing again and returns the same key."""
    app = _app_of(app_or_core)
    if app.extensions.get(STARTED):
        return app.extensions[STARTED]
    core = wire_core(app)
    report = core.caretaker.selftest()
    if report["mode"] == "safe":
        print("Safe mode: " + " ".join(report["reasons"]))
    core.packages.recover_after_restart()
    core.providers.recover_after_restart()
    _update_builtins(core)                    # lane I13: before anything starts, built-ins follow the code
    _clean_try_temps(core.state)              # before Toolsmith's host: no try is open yet
    core.tokens.revoke_device_label(TERMINAL_LABEL)
    core.tokens.revoke_unbound_browsers()     # D79: browser logins from before D79 (no second half) end here
    from .. import wiring
    wiring.start(core, core.notices)          # the Bridge proxy and bypass watch, scopes, card routing
    core.terminal_label = f"{TERMINAL_LABEL} (run {secrets.token_hex(8)})"
    terminal = core.tokens.issue_device(core.terminal_label)
    key_file = core.state / "settings" / "terminal.json"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text(json.dumps({"port": core.port, "pid": os.getpid(), "token": terminal}), encoding="utf-8")
    core.steward.start_saved()
    core.caretaker.start()
    host = start_toolsmith(app)               # lane L6: Toolsmith's host, once
    _open_network(app)
    if core.caretaker.in_safe_mode and callable(getattr(host, "pause", None)):
        host.pause()                          # safe mode at start: Toolsmith stays paused (01 "Core start")
    elif callable(getattr(host, "resume_store_adds", None)):
        host.resume_store_adds()              # lane I16: store adds cut off by a restart go on, then the build
    from ..zone1 import books
    ports = getattr(host, "ports", None)
    books.look(core.state, core.registry, getattr(ports, "screen_knowledge", None))   # D136: a .zim dropped while off
    app.extensions[STARTED] = terminal
    return terminal


def _open_network(app: Flask) -> None:
    """D130: the home network, unless she turned it off. Whatever goes wrong here, the Core still starts: the listeners
    on 127.0.0.1 carry on, and one plain line says what happened. The address is printed, so a bot with no screen says
    what to type on the other device."""
    net = app.extensions[network.EXTENSION]
    net.accepting = True                      # from here her saved setting may open a socket
    try:
        opened = net.open()
        line = (f"Paired devices on your home network can open BuildBot at: {'  '.join(net.view()['urls'])}" if opened
                else net.why)
    except Exception:  # noqa: BLE001 - never stops the Core start
        logging.getLogger(__name__).exception("home network listener")
        line = "BuildBot could not listen on your home network, so paired devices can't reach it. This computer still can."
    if line:
        try:
            print(line, flush=True)
        except (OSError, ValueError):         # no console (a service)
            pass


def _update_builtins(core: Core) -> list[dict]:
    """Each installed built-in against the repo's copy (``PackageManager.check_builtins``): system parts
    updated now, apps get a notice and a Yes card. Never stops the Core start."""
    try:
        results = core.packages.check_builtins(notices=core.notices)
    except Exception:  # noqa: BLE001 - logged; the old copies keep running
        logging.getLogger(__name__).exception("built-in update check")
        return []
    for r in results:
        if r.get("status") != "current" and r.get("line"):
            try:
                print(r["line"], flush=True)
            except (OSError, ValueError):     # no console (a service)
                pass
    return results


def _clean_try_temps(state: Path) -> int:
    r"""The try TEMP folders (``<bot>\t\<6 hex>``) a run before left behind, removed (lane I2a part D)."""
    try:
        from ..zone3.toolsmith_host import broker
    except ImportError:
        return 0
    try:
        return broker.clean_temps(state)
    except OSError:                            # a leftover that can't go now goes at the next start
        return 0


def start_toolsmith(app: Flask) -> Any:
    """Lane L6's Toolsmith host for this Core, made once (``groups.toolsmith.service``); None without L6."""
    try:
        from .groups import toolsmith as toolsmith_group
    except ImportError:
        return None
    with app.app_context():
        return toolsmith_group.service()


def stop_core(core: Core, terminal: str | None) -> None:
    """Core stop: Caretaker stops, every package stops (D18), every Developer command line ends, the
    terminal key ends."""
    from .. import wiring
    from .groups import dev
    app = getattr(core, "app", None)
    if app is not None:
        net = app.extensions[network.EXTENSION]
        net.accepting = False                 # D130: the network goes first
        net.close()
        dev.close_all(app)                    # each shell's job ends, and everything it started
    stop_toolsmith(core)
    if app is not None:
        app.extensions.pop(STARTED, None)
    core.caretaker.stop()
    core.steward.stop_all()
    wiring.stop(core)
    if terminal:
        core.tokens.revoke(terminal)
    core.terminal_label = None
    (core.state / "settings" / "terminal.json").unlink(missing_ok=True)


def stop_toolsmith(core: Core) -> None:
    """Toolsmith's own processes and their tries end, at once (D18). Run before the listeners close too: a
    request that waits on Toolsmith (a plan asked for from the API or chat) then ends before they do."""
    app = getattr(core, "app", None)
    toolsmith = app.extensions.get("puffpai_toolsmith") if app is not None else None
    if toolsmith is not None:
        toolsmith.stop(grace_s=0)


def serve(port: int = PORT, state: Path | str | None = None, pages_port: int = PAGES_PORT,
          proxy_port: int = 8421, lock: Any = None) -> None:
    """Serve the Core API and the pages address on 127.0.0.1, until stopped (Ctrl+C), and the home network unless she
    has turned it off (D130, ``start_core``).

    One Core per bot (D91): *lock* is this bot's ``corelock`` (taken here when not given); it is held until
    the Core has stopped. Raises ``corelock.AlreadyRunning`` before anything starts when another Core runs
    for this bot, and ``corelock.PortInUse`` when a port is in use."""
    from .. import corelock
    state = Path(state) if state else state_dir()
    lock = lock if lock is not None else corelock.acquire(state, port=port, pages_port=pages_port,
                                                          wait_s=corelock.START_WAIT_S)
    try:
        app = create_app(state, port=port, pages_port=pages_port)
        servers = make_servers(app, port, pages_port)     # both bound before anything starts
        core: Core = app.extensions["puffpai"]
        core.proxy_port = proxy_port
        terminal = start_core(app)                    # D57: the one start (Zone 3, wiring, proxy, watch, Toolsmith)
        break_stops()
        stop = accept_shutdown(app)                   # lane I7: puffpai shutdown asks for the same stop as Ctrl+C
        try:
            run_servers(servers, before_stop=lambda: (end_streams(app), stop_toolsmith(core)), stop=stop)
        except KeyboardInterrupt:
            pass
        finally:
            with stopping():
                stop_core(core, terminal)
    finally:
        lock.release()


#: What the terminal says once Ctrl+C (or Ctrl+Break) has stopped the Core and everything it started.
STOPPED_LINE = "BuildBot stopped."
STILL_STOPPING_LINE = "BuildBot is stopping its apps. One moment."


def break_stops() -> None:
    """Ctrl+Break stops BuildBot the way Ctrl+C does (Windows). Without this, Windows ends the process
    at once, so nothing is cleaned up. Only the main thread can set a signal handler."""
    if hasattr(signal, "SIGBREAK") and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGBREAK, signal.default_int_handler)


@contextmanager
def stopping():
    """While the Core stops, another Ctrl+C or Ctrl+Break says to wait instead of cutting the stop short
    (a stop cut short can leave an app running). Main thread only; elsewhere it does nothing."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    names = [n for n in ("SIGINT", "SIGBREAK") if hasattr(signal, n)]
    before = {n: signal.getsignal(getattr(signal, n)) for n in names}

    def wait(_sig, _frame) -> None:
        print(STILL_STOPPING_LINE, flush=True)
    for n in names:
        signal.signal(getattr(signal, n), wait)
    try:
        yield
    finally:
        for n, h in before.items():
            signal.signal(getattr(signal, n), h if h is not None else signal.SIG_DFL)


def end_streams(app: Flask) -> None:
    """The Core is stopping: open ``/v1/events`` streams end now, so the listeners don't wait on them.
    The Developer command lines close here too, so their event streams end with them."""
    feed = app.extensions.get("puffpai.feed")
    if feed is not None:
        feed.end_streams()
    from .groups import dev
    dev.close_all(app)


def accept_shutdown(app: Flask, stop: threading.Event | None = None) -> threading.Event:
    """From now on ``POST /v1/shutdown`` (``puffpai shutdown``) may stop this Core: it sets the event returned
    here (*stop*, or a new one), which :func:`run_servers` watches, and the caller's own stop (the one Ctrl+C
    runs) does the rest."""
    from .groups.shutdown import STOP
    stop = stop if stop is not None else (app.extensions.get(STOP) or threading.Event())
    app.extensions[STOP] = stop
    return stop


def run_servers(servers: Iterable, before_stop: Callable[[], None] | None = None,
                stop: threading.Event | None = None) -> None:
    """Run every listener in a thread and wait here; stop them all when one stops, on Ctrl+C, or when *stop*
    is set (``puffpai shutdown``), with *before_stop* first (e.g. :func:`end_streams`). Waiting here, not in
    waitress's own loop, means Ctrl+C reaches this code first: waitress would otherwise wait up to 5 s for a
    request still open (the Desktop's event stream) before anything else could stop."""
    servers = list(servers)
    threads = [threading.Thread(target=s.run, name=f"puffpai-listener-{i}", daemon=True)
               for i, s in enumerate(servers)]
    for t in threads:
        t.start()
    try:
        while all(t.is_alive() for t in threads) and not (stop is not None and stop.is_set()):
            threads[0].join(0.5)              # a join with a timeout, so Ctrl+C is seen on Windows
    finally:
        if before_stop is not None:
            before_stop()
        busy = stop_servers(servers)
        for s, t in zip(servers, threads):
            if s not in busy:                 # a listener with a request still running is left to the exit
                t.join(timeout=5)


#: How long the stop waits for a request still running (a plan waiting on a brain that does not answer
#: would hold it for minutes). After that the stop goes on; that request ends with the process.
REQUEST_WAIT_S = 1.0


def stop_servers(servers: Iterable) -> list:
    """Let the requests in flight finish (up to :data:`REQUEST_WAIT_S`), then close each listener inside its
    own loop thread (waitress's trigger), so no select or write ever meets a closed socket (a WinError 10038
    trace in her window). A listener with a request still running only stops listening: it keeps its
    wake-up socket and connections, so that request's last write goes nowhere quietly when the process ends.
    Returns the listeners that still had a request running."""
    import logging as _logging
    from waitress import wasyncore
    servers = list(servers)
    quiet = _logging.getLogger("waitress")
    level = quiet.level
    quiet.setLevel(_logging.ERROR)            # not "1 thread(s) still running" in her window
    try:
        for s in servers:
            dispatcher = getattr(s, "task_dispatcher", None)
            if dispatcher is not None:
                dispatcher.shutdown(timeout=REQUEST_WAIT_S)
    finally:
        quiet.setLevel(level)
    busy = []
    for s in servers:
        running = bool(getattr(getattr(s, "task_dispatcher", None), "threads", None))
        if running:
            busy.append(s)

        def close(s=s, running=running) -> None:
            try:
                if running:
                    wasyncore.dispatcher.close(s)      # stop listening only
                else:
                    wasyncore.close_all(s._map)        # everything: its loop then ends  # noqa: SLF001
            except OSError:
                pass
        trigger = getattr(s, "trigger", None)
        try:
            if trigger is not None:
                trigger.pull_trigger(close)            # run in the listener's own loop thread
            else:
                s.close()
        except OSError:                                # already closed (a second stop): nothing to do
            pass
    return busy
