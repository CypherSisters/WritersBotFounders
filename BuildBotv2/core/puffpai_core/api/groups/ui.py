"""Core API group `ui` (lane L5): the active ui package at the root address, its files,
the plain recovery page, and pairing a browser.

- ``GET /``: the active static ui package's ``index.html`` (a ui with its own
  process is forwarded like any page, so ``/`` sends the browser to ``/pkg/<id>/``
  on the pages address, D49, where it has no owner login).
- ``GET /ui/<path>``: the active static ui package's other files. Static
  files hold no owner data; everything the Desktop shows comes from ``/v1``.
- ``GET /window/``, ``GET /window/<path>`` (D108): the PuffPAI window's page. Only the built-in default Desktop's
  files, never the active ui and never a Registry row, so a third-party Desktop can't run in the window.
- ``GET /v1/recover``: a plain page that always works, so a broken UI can't
  lock the owner out (04 section 5).
- ``GET /v1/signin``: where ``Sign in a browser.cmd`` is (its file name and folder, D92), for the
  Desktop's sign-in screen. Not secret.
- ``POST /v1/pair``: a one-time code from ``puffpai pair`` becomes the
  paired-device cookie (``pairing.py``) and, in the JSON body, its second half
  ``device_key`` (D79), which the Desktop sends as ``X-PuffPAI-Device``.
- ``GET /pair`` (D130): the home network's pair screen. It is no route on the 127.0.0.1 listeners.

D79: all of these answer only at ``http://puffpai.localhost:<port>``. On
``127.0.0.1`` or ``localhost`` the gate (``api.app``) answers the pages with
``moved_page`` and refuses ``/v1/pair``, so the cookie is set only on the
Desktop's own name, host-only (no ``Domain``), where no app on 127.0.0.1 gets
it. No ``__Host-`` prefix: it needs ``Secure``, which plain http can't give.

D130: unless she has turned it off, the network listener (``api.network``) serves the Desktop's own files and the window's
page to a paired browser, the pair screen and ``/v1/pair`` to anyone there, and the same cookie and second half, host-only
on the address that device really uses. The gate (``api.app._network_gate``) decides; this file only words the answers.
"""

import os
import secrets
import shlex
from html import escape
from pathlib import Path

from flask import g, Blueprint, Response, current_app, jsonify, redirect, request, send_from_directory
from werkzeug.security import safe_join

from ... import desktop, pairing
from .. import auth
from ..auth import PUBLIC
from ..network import EXTENSION, NETWORK_LISTENER, WORTH_KNOWING, cookie_name
from ..routing import current_core, route

bp = Blueprint("ui", __name__)


def _root() -> tuple[str, Path | None]:
    core = current_core()
    if _on_network():       # D130: only the built-in Desktop, as D108 for the window: no other code on the second half's origin
        return desktop.DEFAULT_UI, desktop.static_root(core.state, desktop.DEFAULT_UI, None)
    ui_id = desktop.active_ui(core.state, core.registry)
    return ui_id, desktop.static_root(core.state, ui_id, core.registry)


def _on_network() -> bool:
    return request.environ.get("puffpai.listener") == NETWORK_LISTENER


@route(bp, "GET", "/", access=PUBLIC)
def ui_root():
    ui_id, root = _root()
    if _on_network():
        current_app.extensions[EXTENSION].page_ports()      # the Desktop's page ports, known before its CSP is written
        if root is None:
            return redirect("/v1/recover")        # a Desktop with its own program has no address here
    if root is None:
        if desktop.package_dir(current_core().state, ui_id, current_core().registry) is not None:
            return redirect(f"{current_app.config['PUFFPAI_PAGES_ORIGIN']}/pkg/{ui_id}/")
        return redirect("/v1/recover")
    resp = send_from_directory(root, "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "GET", "/ui/<path:path>", access=PUBLIC)
def ui_file(path):
    _, root = _root()
    if root is None or safe_join(str(root), path) is None:
        return jsonify(error="No such file."), 404
    resp = send_from_directory(root, path)
    resp.headers["Cache-Control"] = "no-store"
    return resp


#: What the window's page is made of, each with its type (Windows' own table can say text/plain for .js). Nothing else
#: is served there: not another page, not .svg or .xhtml, which a browser would also run as a document.
_WINDOW_TYPES = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2", ".jpg": "image/jpeg",
                 ".webp": "image/webp"}


def _window_file(path: str):
    """D108. ``registry=None``: the built-in's own folder, whatever is installed or active. Decided on what the path
    really is on disk, not on its spelling: Windows finds ``index.html/``, ``index.html.`` and ``INDEX~1.HTM`` too.
    Its one page is ``window.html``: the dashboard's page names ``/ui/`` files, which are the active Desktop's."""
    no = jsonify(error="No such file."), 404
    root = desktop.static_root(current_core().state, desktop.DEFAULT_UI, None)
    full = safe_join(str(root), path) if root is not None and not path.endswith("/") and ":" not in path else None
    if full is None:
        return no
    real, base = Path(os.path.realpath(full)), Path(os.path.realpath(root))
    if not real.is_file() or base not in real.parents:
        return no
    rel = real.relative_to(base).as_posix()
    mimetype = "text/html" if rel == "window.html" else _WINDOW_TYPES.get(real.suffix.lower())
    if mimetype is None or path != rel:           # another spelling of a real file is no name for it either
        return no
    resp = send_from_directory(base, rel, mimetype=mimetype)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@route(bp, "GET", "/window/", access=PUBLIC)
def window_root():
    if _on_network():                             # D130: a browser on another device stands in for the window
        current_app.extensions[EXTENSION].page_ports()
    return _window_file("window.html")


@route(bp, "GET", "/window/<path:path>", access=PUBLIC)
def window_file(path):
    return _window_file(path)


RECOVER = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PuffPAI: recovery</title>
<style nonce="{nonce}">
body {{ font: 15px/1.5 system-ui, sans-serif; background: #f5efe3; color: #1f1c1a; max-width: 40em; margin: 2em auto; padding: 0 1em; }}
h1 {{ font: 600 1.6em Georgia, serif; border-bottom: 2px solid #1f1c1a; padding-bottom: .2em; }}
code {{ background: #ece4d3; padding: 0 .3em; }} button {{ font: inherit; padding: .3em .8em; }}
footer {{ margin-top: 3em; font-size: .8em; color: #5a534c; }}
</style>
<h1>Recovery</h1>
<p>This page always works, even when a Desktop does not.</p>
<p>The active Desktop is <b>{ui}</b>.</p>
<p><button id="reset">Use the PuffPAI Desktop</button> <span id="said"></span></p>
<p>{signin}</p>
<p>Other things to try: <code>puffpai list</code> shows everything installed, <code>puffpai setup</code> runs setup in the terminal.</p>
<p>Nothing leaves your computer without your consent.</p>
<footer>PuffPAI by Cypher Sisters. puff puff pass</footer>
<script nonce="{nonce}">
document.getElementById("reset").onclick = async () => {{
  const headers = {{"Content-Type": "application/json", "X-PuffPAI": "1"}};
  try {{ const k = localStorage.getItem("puffpai.device_key"); if (k) headers["X-PuffPAI-Device"] = k; }} catch (e) {{ }}
  const r = await fetch("/v1/settings/ui", {{method: "PUT", headers: headers, body: JSON.stringify({{id: "desktop"}})}});
  const said = document.getElementById("said");
  said.textContent = r.ok ? "Done. Open the Desktop: /" : ((await r.json()).error || "That did not work.");
}};
</script>
</html>
"""


def signin_where() -> dict:
    """D92: the file that signs a browser in (``Sign in a browser.cmd``, written by install.ps1) and its folder,
    or empty strings when there is none (run from source). Not secret: the Desktop's sign-in screen names it."""
    from ...wizard.terminal import cli_command, signin_launcher
    f = signin_launcher()
    if f.is_file() and os.name != "nt":                 # D132: nothing to double-click, the launcher is the command
        return {"file": "", "folder": "", "command": shlex.quote(str(f))}
    out = {"file": f.name, "folder": str(f.parent)} if f.is_file() else {"file": "", "folder": ""}
    # With no launcher, the one command that runs here: bare "puffpai" is on no PATH (D92(e)).
    return dict(out, **({} if f.is_file() else {"command": cli_command("pair", "--running")}))


def signed_in_where() -> dict:
    """For a browser already signed in, on top of :func:`signin_where`: how to chat in a terminal, and this install's
    own program for Developer's Use CLI. Only she needs them, so a signed-out page never gets them."""
    from ...wizard.terminal import chat_launcher, cli_command
    chat = chat_launcher()
    if chat.is_file() and os.name != "nt":              # D132: as signin_where
        return {"chat_file": "", "chat_folder": "", "chat_command": shlex.quote(str(chat)), "cli": cli_command(),
                "shells": ["bash", "sh"]}
    return {"chat_file": chat.name if chat.is_file() else "", "chat_folder": str(chat.parent) if chat.is_file() else "",
            "chat_command": "" if chat.is_file() else cli_command(), "cli": cli_command(),
            "shells": ["powershell", "cmd"] if os.name == "nt" else ["bash", "sh"]}


def signin_line() -> str:
    """The one plain line that says how to sign a browser in (the Desktop's ``logic.signInWords`` says the same)."""
    w = signin_where()
    if w["file"]:
        return f"To sign in this browser, double-click {w['file']} in {w['folder']}, then open the link it shows."
    return f"To sign in this browser, run this in a terminal on this computer, then open the link it shows: {w['command']}"


@route(bp, "GET", "/v1/signin", access=PUBLIC)
def ui_signin():
    """D92: where the owner signs a browser in, for the Desktop's sign-in screen. Only at the Desktop's own
    address (``_DESKTOP_PAGES``), like the recovery page."""
    signed_in = signed_in_where() if getattr(g, "caller", None) is not None else {}
    if _on_network() and getattr(g, "caller", None) is None:   # D130: an unpaired device learns no folder and no command
        resp = jsonify(file="", folder="", pair="/pair", **signed_in)
        resp.headers["Cache-Control"] = "no-store"
        return resp
    resp = jsonify(dict(signin_where(), **signed_in))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "GET", "/v1/recover", access=PUBLIC)
def ui_recover():
    ui_id, _ = _root()
    # D130: a page load proves only the cookie, so on the network it names no package and no folder.
    ui_id, line = ("the built-in one", "To sign in this device, open /pair.") if _on_network() else (ui_id, signin_line())
    nonce = secrets.token_urlsafe(16)
    resp = Response(RECOVER.format(ui=escape(ui_id), nonce=nonce, signin=escape(line)), mimetype="text/html")
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["Cache-Control"] = "no-store"
    return resp


PAIR_SCREEN = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>PuffPAI: pair this device</title>
<style nonce="{nonce}">
body {{ font: 16px/1.5 system-ui, sans-serif; background: #f5efe3; color: #1f1c1a; max-width: 32em; margin: 2em auto; padding: 0 1em; }}
h1 {{ font: 600 1.6em Georgia, serif; border-bottom: 2px solid #1f1c1a; padding-bottom: .2em; }}
input {{ font: inherit; padding: .4em; width: 100%; box-sizing: border-box; text-transform: uppercase; }}
button {{ font: inherit; padding: .4em 1em; margin-top: .6em; }} footer {{ margin-top: 3em; font-size: .8em; color: #5a534c; }}
.small {{ font-size: .9em; color: #5a534c; }}
</style>
<h1>Pair this device</h1>
<p>A pairing code lets this device use BuildBot.</p>
<ol>
<li>Go to the computer BuildBot runs on.</li>
<li>Open Settings, then Your home network, and press Show a pairing code.</li>
<li>Type the code here.</li>
</ol>
<form id="pair"><input id="code" autocomplete="off" autocapitalize="characters" spellcheck="false" aria-label="Pairing code" placeholder="XXXX-XXXX-XXXX">
<button>Pair</button></form>
<p id="said" role="status"></p>
<p>If that computer has no screen, ask whoever set it up to run: <b>puffpai pair</b></p>
<p class="small">{worth}</p>
<footer>PuffPAI by Cypher Sisters. puff puff pass</footer>
<script nonce="{nonce}">
const said = document.getElementById("said");
document.getElementById("pair").addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  said.textContent = "";
  let r = null, d = null;
  try {{
    r = await fetch("/v1/pair", {{method: "POST", credentials: "same-origin",
      headers: {{"Content-Type": "application/json", "X-PuffPAI": "1"}},
      body: JSON.stringify({{code: document.getElementById("code").value}})}});
    d = await r.json();
  }} catch (e) {{ d = null; }}
  if (!r || !r.ok) {{ said.textContent = (d && d.error) || "BuildBot did not answer. Try again."; return; }}
  try {{ localStorage.setItem("puffpai.device_key", d.device_key); }} catch (e) {{
    said.textContent = "This browser does not let BuildBot save site data, so it can't stay paired. Try a window that is not private.";
    return;
  }}
  location.replace(new URLSearchParams(location.search).get("back") === "window" ? "/window/" : "/");
}});
</script>
</html>
"""


@route(bp, "GET", "/pair", access=PUBLIC)
def ui_pair_screen():
    """D130: the pair screen on the home network. It holds no secret and names nothing of hers: it reaches unpaired
    devices, so it says "BuildBot", never the name she gave her bot. The Core's own page, never the active Desktop's
    script, so a code is typed only into this. Its steps name the Settings tile's own words (a test checks them)."""
    nonce = secrets.token_urlsafe(16)
    resp = Response(PAIR_SCREEN.format(nonce=nonce, worth=escape(WORTH_KNOWING)), mimetype="text/html")
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@route(bp, "POST", "/v1/pair", access=PUBLIC)
def ui_pair():
    body = request.get_json(silent=True) or {}
    core = current_core()
    net = current_app.extensions[EXTENSION] if _on_network() else None
    peer = request.environ.get("REMOTE_ADDR") or ""
    if net is not None and not net.limit.take(peer):       # D130: the socket's own peer, never a forwarded header
        return jsonify(error="Too many wrong codes. Wait a few minutes, then try again.", code="pair_wait"), 429
    if not pairing.redeem(core.state, body.get("code") if isinstance(body, dict) else None):
        if net is not None:
            return jsonify(error="That pairing code is wrong, used or older than 10 minutes. Get a new one from "
                                 "the computer running your bot.", code="pair_code"), 401
        return jsonify(error="That pairing code is wrong, used or older than 10 minutes. "
                             "Run puffpai pair for a new one.", code="pair_code"), 401
    if net is not None:
        net.limit.forgive(peer)                            # it was right: this try does not count
    label = str(request.headers.get("User-Agent", "browser"))[:60]
    token = core.tokens.issue_device(f"{auth.BROWSER_LABEL}{label}")
    # D79: the second half of the login, in the body only (never a cookie, the URL, a log or an event). The
    # Desktop keeps it in its own origin's localStorage, which no other port can read, and sends it as
    # X-PuffPAI-Device; the cookie alone then counts for nothing.
    second = core.tokens.bind_device(token)
    resp = jsonify(ok=True, device_key=second)
    resp.headers["Cache-Control"] = "no-store"
    # Host-only (no Domain), on puffpai.localhost only (the gate refuses pairing anywhere else, D79), under this Core's
    # own name (D129), so another bot's sign-in in the same browser doesn't replace it. Or on the home network's own
    # address, under that listener's own name, so two bots on one address stay apart (D130).
    resp.set_cookie(cookie_name(net.port) if net is not None else auth.cookie_name(core.port), token, httponly=True,
                    samesite="Strict", path="/", max_age=400 * 24 * 3600)
    return resp
