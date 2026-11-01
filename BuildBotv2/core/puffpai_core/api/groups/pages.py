"""Core API group `pages` (lane L1, built). Package pages through the Core (02 'Pages go through the Core', D49, D53).

The gate in ``api/app.py`` (lane L5) serves these routes on the pages listener
(port 8422) only: 404 on the Core's own address, no owner login on the pages
listener (the device cookie is never read, owner keys are refused), so the
access level is ``public`` and the forwarding itself adds the token.

**Every package page has its own origin (D53): ``http://<id>.localhost:8422``.**
``api.app`` routes a request with that ``Host`` here as ``/pkg/<id>/<path>``
(``environ["puffpai.page_package"]``). It is forwarded to
``127.0.0.1:{its port}/<path>``: the package comes from the Host only, the
request goes to that package's own port only, with that package's own token
(``Authorization: Bearer``). Taken out of the request: ``Cookie``, any
incoming ``Authorization``, ``X-PuffPAI-Key``, ``X-PuffPAI`` and ``X-PuffPAI-Device`` (D79). Taken out of
the reply: ``Set-Cookie`` and the other login, cookie-clearing and
cross-origin headers. The package is told its own origin as ``Host``
(``<id>.localhost:8422``), so the links it builds are on that origin.
Redirects are never followed by the Core. A redirect to a relative place
(``/login``) or to the package's own origin goes back unchanged; one to
anywhere else (another origin, ``//host``, a backslash or control-character
trick) is refused with 502.

Rules on a package origin:
- A request that is not GET, HEAD or OPTIONS must come from that package's
  own origin (``Origin``, or with no Origin, ``Sec-Fetch-Site: same-origin``).
  ``Origin: null``, another package's origin, the Core's own and any other
  website get 403. So no package page posts to another, and no website drives
  a package through a sandboxed frame. GET, POST, PUT and DELETE are forwarded (PUT and DELETE since
  lane I13, under the same rules as POST); a static package's files take no PUT or DELETE (405). A body
  over ``MAX_BODY`` (25 MB, FreshTable's own limit) gets 413 and is never forwarded.
- A GET from the Desktop's own origin gets ``Access-Control-Allow-Origin:
  <that origin>`` (widget JSON), never with credentials. Nothing else gets a
  cross-origin answer, so no other page can read a package's replies.

Lane I18c: a page opened in the Desktop's frame (``Sec-Fetch-Dest: iframe`` or ``document``, or an
``Accept`` with ``text/html``) never gets raw JSON. While its app is starting (being added, or its server
not listening yet) it gets one plain line, "<name> is starting...", and the page asks again by itself every
few seconds; stopped or not working, a plain line that says so and points to Apps, and it asks again too.
Every other request (a widget's JSON, a page's own fetch) gets the same JSON as before. A server app's own
pictures (its icon and tray icons, ``_pictures``) are served by the Core from its folder, checked as the
store's icons are (``packages._icon_data``), since its server does not serve them.

On the home network (D130, ``api.network``), a package's page port forwards the same way, under the same rules, with
that port's own origin in place of ``<id>.localhost``. The gate (``api.app._network_page_gate``) has already seen that
package's pass; it is a cookie, so it never reaches the package either. A GET from the Desktop's network origin may read
the answer with credentials: there the widget JSON needs the pass.

``/pkg/<id>/<path>`` on ``127.0.0.1:8422`` itself (the old D49 path): a GET is
redirected (302) to ``http://<id>.localhost:8422/<path>``; anything else, or
another Host, gets 404 or 403. A ``static`` package's files are served by the
Core itself, from its ``run.dir`` folder only.
"""

from __future__ import annotations

import base64
import html
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import Blueprint, Response, current_app, jsonify, redirect, request, send_from_directory

from ...steward.steward import run_block
from ..auth import PUBLIC
from ..network import EXTENSION, NETWORK_PAGE_LISTENER
from ..routing import current_core, route

bp = Blueprint("pages", __name__)

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
        "transfer-encoding", "upgrade", "host", "content-length"}
_NEVER_FORWARD_EXTRA = {"x-forwarded-prefix", "x-forwarded-host", "x-forwarded-for", "forwarded"}
_NEVER_FORWARD = {"authorization", "cookie", "x-puffpai-key", "x-puffpai", "x-puffpai-device"} | _NEVER_FORWARD_EXTRA
#: Never passed back from a package: logins, auth prompts, cookie clearing and cross-origin grants.
_NEVER_RETURN = {"set-cookie", "set-cookie2", "authorization", "www-authenticate", "proxy-authenticate",
                 "x-puffpai-key", "clear-site-data", "access-control-allow-origin",
                 "access-control-allow-credentials", "access-control-allow-headers",
                 "access-control-allow-methods", "access-control-expose-headers", "access-control-max-age",
                 "access-control-allow-private-network"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _guard(id: str, path: str):
    """None if the request may be forwarded to package *id*, else the answer (refusal or redirect)."""
    from ..app import LISTENER, PAGE_PACKAGE, PAGES_LISTENER, _PACKAGE_ID, package_origin
    if request.environ.get(LISTENER) not in (PAGES_LISTENER, NETWORK_PAGE_LISTENER):
        return jsonify(error="Package pages are served from the pages address only."), 404
    pages = current_app.config["PUFFPAI_PAGES_ORIGIN"]
    own_id = request.environ.get(PAGE_PACKAGE)
    if own_id is None:                                        # 127.0.0.1:8422/pkg/<id>/...: the old path
        if f"http://{request.host}" != pages:
            return jsonify(error=f"Package pages answer only at their own address (D53)."), 403
        if request.method not in ("GET", "HEAD") or not _PACKAGE_ID.fullmatch(id):
            return jsonify(error="Package pages live at http://<id>.localhost:8422 (D53)."), 404
        target = f"{package_origin(id, pages)}/{path}"
        if request.query_string:
            target += "?" + request.query_string.decode("latin-1")
        return redirect(target, code=302)
    if own_id != id:                                          # never: the app sets both from the Host
        return jsonify(error="No such package page."), 404
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        own = _own_origin(id)
        origin = request.headers.get("Origin")
        if origin is not None:
            ok = origin.strip().rstrip("/").lower() == own
        else:
            ok = request.headers.get("Sec-Fetch-Site", "").strip().lower() == "same-origin"
        if not ok:
            return jsonify(error="Only this package's own page can send to it."), 403
    return None


def _own_origin(id: str) -> str:
    """A package's own origin: ``http://<id>.localhost:<pages port>`` (D53), or the page port it came in on (D130)."""
    from ..app import LISTENER, package_origin
    if request.environ.get(LISTENER) == NETWORK_PAGE_LISTENER:
        return f"http://{request.host.lower()}"       # checked before any route: its own address or name, its own port
    return package_origin(id, current_app.config["PUFFPAI_PAGES_ORIGIN"])


def _location_ok(value: str, own: str) -> bool:
    """A redirect stays on the package's own origin: relative, or absolute on *own*."""
    v = value.strip()
    if not v or any(ch in v for ch in "\\\t\r\n") or any(ord(ch) < 0x20 for ch in v) or v.startswith("//"):
        return False
    try:
        parts = urllib.parse.urlsplit(v)
    except ValueError:
        return False
    if not parts.scheme and not parts.netloc:
        return True
    return f"{parts.scheme}://{parts.netloc}".lower() == own


def _cors(resp: Response) -> Response:
    from ..app import LISTENER
    origin = (request.headers.get("Origin") or "").strip().rstrip("/").lower()
    if request.environ.get(LISTENER) == NETWORK_PAGE_LISTENER:     # D130: the Desktop's own network origin, and only it
        if request.method == "GET" and origin == current_app.extensions[EXTENSION].desktop_origin(request.host):
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers.add("Vary", "Origin")
        return resp
    if request.method == "GET" and origin in current_app.config["PUFFPAI_CORE_ORIGINS"]:
        resp.headers["Access-Control-Allow-Origin"] = origin      # the Desktop's widget JSON, no credentials
        resp.headers.add("Vary", "Origin")
    return resp


def _pictures(manifest: dict) -> set[str]:
    """The files the Desktop loads for a package with no server of its own: its icon, its tray icons and
    its avatar's preview and model (``desktop.avatar_view`` names them ``/pkg/<id>/<file>``)."""
    names = [manifest.get("icon")] + [t.get("icon") for t in (manifest.get("ui") or {}).get("tray", [])
                                      if isinstance(t, dict)]
    avatar = manifest.get("avatar") or {}
    names += [avatar.get("preview"), avatar.get("model")] if isinstance(avatar, dict) else []
    return {str(n).replace("\\", "/") for n in names if isinstance(n, str) and n}


#: How long after its start a server app that does not answer yet counts as starting (Caretaker's start grace).
START_GRACE_S = 120
#: How often a waiting page asks again, in seconds.
RETRY_S = 3


def _wants_page() -> bool:
    """True for a page opened in a frame or a tab (never for a widget's JSON or a page's own fetch)."""
    dest = request.headers.get("Sec-Fetch-Dest", "").strip().lower()
    if dest:
        return dest in ("document", "iframe", "frame")
    return request.method in ("GET", "HEAD") and "text/html" in request.headers.get("Accept", "").lower()


def _say(line: str, status: int, retry: bool):
    """*line* for the owner: a plain page in her frame (asking again by itself when *retry*), else JSON."""
    if not _wants_page():
        return jsonify(error=line), status
    again = f'<meta http-equiv="refresh" content="{RETRY_S}">' if retry else ""
    body = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{again}<title>{html.escape(line)}</title>'
            '<style>html{color-scheme:light dark}body{font:16px/1.5 system-ui,sans-serif;margin:0;display:flex;'
            'min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px;box-sizing:border-box}'
            'p{margin:0;max-width:32em}</style></head>'
            f'<body><p>{html.escape(line)}</p></body></html>')
    resp = Response(body, status=status, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _not_up(name: str, status: str, starting: bool):
    if starting or status in ("installing", "running"):
        return _say(f"{name} is starting...", 503, True)
    if status in ("broken", "needs_you"):
        return _say(f"{name} is not working right now. Open Apps to see why.", 503, True)
    return _say(f"{name} is not running. Start it from Apps.", 503, True)


def _picture(folder, manifest: dict, path: str):
    """A server app's own picture (its icon or a tray icon), from its folder, or None."""
    if path not in _pictures(manifest):
        return None
    from .packages import _icon_data
    data = _icon_data(folder, path)
    if data is None:
        return None
    head, b64 = data.split(",", 1)
    resp = Response(base64.b64decode(b64), mimetype=head[5:].split(";", 1)[0])
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return _cors(resp)


#: The most a page may send its app in one request (lane I13): FreshTable's own limit (its server.py
#: MAX_BODY), since its page sends a phone photo whole, as a data URL. Above it: 413, nothing forwarded.
MAX_BODY = 25 * 1024 * 1024


def _capped_body() -> bytes | None:
    """The request body, or None when it is over :data:`MAX_BODY` (by its Content-Length, or by reading
    at most one byte more when it has none). Never reads more than MAX_BODY + 1 bytes."""
    length = request.content_length
    if length is not None and length > MAX_BODY:
        return None
    parts, got = [], 0
    while got <= MAX_BODY:
        chunk = request.stream.read(min(1 << 20, MAX_BODY + 1 - got))
        if not chunk:
            break
        parts.append(chunk)
        got += len(chunk)
    return None if got > MAX_BODY else b"".join(parts)


def _forward(id: str, path: str):
    refused = _guard(id, path)
    if refused:
        return refused
    core = current_core()
    row = core.registry.get(id)
    if row is None:
        return jsonify(error=f"Nothing installed with id {id!r}."), 404
    manifest = core.steward.read_manifest(id)       # the Core's one reader (her Yes, by its sha256)
    if manifest is None:
        return _say(f"{row['name']} can't be opened: its settings file can't be used. Add it again.", 409, False)
    block = run_block(manifest)
    if block is None or block["type"] == "static":
        if request.method in ("PUT", "DELETE"):
            return jsonify(error=f"{row['name']} is plain files: nothing there takes a {request.method}."), 405
        folder = core.steward.folder(id)
        ui = manifest.get("ui") or {}
        declared = {p["file"].replace("\\", "/") for p in ui.get("pages", []) + ui.get("widgets", []) if "file" in p}
        declared |= _pictures(manifest)
        if path == "" or path.endswith("/"):
            index = path + "index.html"         # a folder path: its index.html, when the page has one
            if index in declared:
                path = index
        if path in declared:
            return _cors(send_from_directory(folder, path))
        if block is not None:
            return _cors(send_from_directory(folder / block["dir"], path or "index.html"))
        return jsonify(error=f"{row['name']} has no file {path!r}."), 404
    if request.method in ("GET", "HEAD"):
        pic = _picture(core.steward.folder(id), manifest, path)
        if pic is not None:
            return pic                                         # its icon: from its folder, running or not
    info = core.steward.info(id)
    if info is None or not info.port or not core.steward.alive(id):
        return _not_up(row["name"], row.get("status") or "", False)
    url = f"http://127.0.0.1:{int(info.port)}/{path}"
    if request.query_string:
        url += "?" + request.query_string.decode("latin-1")
    own = _own_origin(id)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP | _NEVER_FORWARD}
    headers["Host"] = own.split("//", 1)[1]                  # the package's own origin (D53)
    if info.token:
        headers["Authorization"] = f"Bearer {info.token}"
    body = None
    if request.method not in ("GET", "HEAD"):
        body = _capped_body()
        if body is None:
            return jsonify(error=f"That is too big to send to {row['name']} "
                                 f"(at most {MAX_BODY // (1024 * 1024)} MB)."), 413
    req = urllib.request.Request(url, data=body, headers=headers, method=request.method)
    try:
        resp = _OPENER.open(req, timeout=60)
    except urllib.error.HTTPError as exc:
        resp = exc
    except OSError:
        if time.time() - info.started_at < START_GRACE_S:      # started, not listening yet
            return _not_up(row["name"], row.get("status") or "", True)
        return _say(f"{row['name']} did not answer. Open Apps to see why.", 502, True)
    with resp:
        data = resp.read()
        status = resp.status if hasattr(resp, "status") else resp.code
        out = []
        for k, v in resp.headers.items():
            if k.lower() in _HOP or k.lower() in _NEVER_RETURN:
                continue
            if k.lower() == "location" and not _location_ok(v, own):
                return jsonify(error=f"{row['name']} tried to send you away from its own page, "
                                     "so the Core did not follow it."), 502
            out.append((k, v))
    return _cors(Response(data, status=status, headers=out))


@route(bp, "GET", "/pkg/<id>/", access=PUBLIC)
def pkg_home(id):
    return _forward(id, "")


@route(bp, "POST", "/pkg/<id>/", access=PUBLIC)
def pkg_home_post(id):
    return _forward(id, "")


@route(bp, "GET", "/pkg/<id>/<path:path>", access=PUBLIC)
def pkg_get(id, path):
    return _forward(id, path)


@route(bp, "POST", "/pkg/<id>/<path:path>", access=PUBLIC)
def pkg_post(id, path):
    return _forward(id, path)


# PUT and DELETE (lane I13): a page that saves with PUT, like FreshTable's, reaches its own app. The same
# _forward, so the same rules as POST: its own origin only, its own port, its own token. PATCH stays 405.
@route(bp, "PUT", "/pkg/<id>/", access=PUBLIC)
def pkg_home_put(id):
    return _forward(id, "")


@route(bp, "PUT", "/pkg/<id>/<path:path>", access=PUBLIC)
def pkg_put(id, path):
    return _forward(id, path)


@route(bp, "DELETE", "/pkg/<id>/", access=PUBLIC)
def pkg_home_delete(id):
    return _forward(id, "")


@route(bp, "DELETE", "/pkg/<id>/<path:path>", access=PUBLIC)
def pkg_delete(id, path):
    return _forward(id, path)
