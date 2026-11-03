"""Core API group `packages` (lane L1, built). The Package Manager and Steward (01 'Package Manager').

``POST /v1/packages`` ``{"source", "link"}`` raises the one Yes card and
answers 202 with its ``consent_id`` (the install runs after the owner's Yes,
``POST /v1/consent/{id}/answer``). ``DELETE /v1/packages/<id>?keep_data=keep|delete``
removes (409 with the question when the data choice is still open).
``start``, ``stop``, ``update`` and ``logs`` do what they say. ``open/<page>`` (a page or
widget id) answers the page's link on its own origin, ``http://<id>.localhost:8422`` (D53).
"""

from __future__ import annotations

import re
import subprocess
import urllib.parse
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request

from ...packages.manager import AddError
from ...packages.manifest import adopted_origin
from ...steward.steward import StewardError, fill, run_block
from ..auth import OWNER, OWNER_DEVICE
from ..network import EXTENSION, NETWORK_LISTENER, ON_COMPUTER
from ..routing import current_core, route

bp = Blueprint("packages", __name__)


def _problem(exc: AddError):
    return jsonify(error=exc.problems[0], problems=exc.problems), exc.status


def _row_or_404(id: str):
    row = current_core().registry.get(id)
    if row is None:
        return None, (jsonify(error=f"Nothing installed with id {id!r}."), 404)
    return row, None


@route(bp, "POST", "/v1/packages", access=OWNER)
def packages_add():
    body = request.get_json(silent=True) or {}
    source = body.get("source")
    if not isinstance(source, str) or not source.strip():
        return jsonify(error='Send {"source": "<folder or git URL>", "link": false}.'), 400
    try:
        staged = current_core().packages.add(source, link=bool(body.get("link")))
    except AddError as exc:
        return _problem(exc)
    return jsonify(staged), 202


@route(bp, "GET", "/v1/packages/available", access=OWNER)
def packages_available():
    """The packages that ship with PuffPAI and are not installed yet (lane I8b, for the Desktop's Apps
    page). Add one with ``POST /v1/packages {"source": "<its id>"}``, the same Yes card as any folder."""
    from ...packages.store_apps import available
    return jsonify(available(current_core().registry))


#: A built-in package's icon for the PuffPAI store (lane I10a): a picture file in its folder, sent as a
#: data: URL, since a package that is not installed has no origin of its own yet. Shown only in an <img>.
#: The file must be a real file inside the folder (no link or junction anywhere on its path, lane I13), at
#: most _ICON_MAX bytes, and its bytes must be the picture type its name says (never the name alone).
_ICON_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg",
               ".jpeg": "image/jpeg"}
_ICON_MAX = 64 * 1024
_ICON_NAME_MAX = 200


def _icon_kind(data: bytes) -> str | None:
    """The picture type of *data* from its own bytes, or None: PNG, WEBP and JPEG by their magic numbers,
    SVG only when it parses with an ``<svg>`` root and nothing in it can run or load (``_svg_ok``)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "image/svg+xml" if _svg_ok(data) else None


_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"


def _local(name: str) -> str:
    return name.rsplit("}", 1)[-1].lower()


#: The one instruction an icon may have: a plain XML declaration, first. Any other ``<?...?>`` anywhere
#: (``<?xml-stylesheet?>`` included) is refused.
_XML_DECL = re.compile(rb"""<\?xml\s+version\s*=\s*(["'])1\.[0-9]\1"""
                       rb"""(?:\s+encoding\s*=\s*(["'])[A-Za-z][A-Za-z0-9._-]{0,39}\2)?"""
                       rb"""(?:\s+standalone\s*=\s*(["'])(?:yes|no)\3)?\s*\?>""")
_CSS_URL = re.compile(r"url\s*\(\s*(?![\"']?#)", re.I)
#: CSS that can load a picture without url(...): image-set() (and its -webkit- form), image(), cross-fade(),
#: element().
_CSS_LOADS = re.compile(r"(?:image-set|image|cross-fade|element)\s*\(", re.I)


def _css_bad(text: str) -> bool:
    """Style text that could load anything: a backslash (CSS escapes hide url( and @import), a url(...)
    that is not ``url(#inside)``, an @import, or image-set() and the like."""
    return "\\" in text or bool(_CSS_URL.search(text)) or "@import" in text.lower() or bool(_CSS_LOADS.search(text))


def _svg_ok(data: bytes) -> bool:
    """A plain SVG drawing: parseable, an ``<svg>`` root, and no script, no ``on*`` attribute, no
    foreignObject, no href that is not ``#inside``, no style or attribute that could load anything
    (``_css_bad``), no animation that sets an href or a handler, no DOCTYPE or entity, and no processing
    instruction but a plain XML declaration first."""
    import xml.etree.ElementTree as ET
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    low = head.lower()
    if b"<!doctype" in low or b"<!entity" in low:
        return False                       # no entities: nothing expands and nothing is fetched
    decl = _XML_DECL.match(head)
    rest = head[decl.end():] if decl else head
    if b"<?" in rest:
        return False                       # an xml-stylesheet or any other instruction, anywhere
    try:
        root = ET.fromstring(head)
    except (ET.ParseError, ValueError):
        return False
    if root.tag not in ("svg", f"{{{_SVG_NS}}}svg"):
        return False
    for el in root.iter():
        if not isinstance(el.tag, str):
            return False
        tag = _local(el.tag)
        if tag in ("script", "foreignobject", "iframe", "embed", "object"):
            return False
        if tag == "style" and _css_bad("".join(el.itertext())):
            return False
        for name, value in el.attrib.items():
            attr = _local(name)
            if attr.startswith("on"):
                return False
            if attr == "href" and not value.strip().startswith("#"):
                return False
            if _css_bad(value):            # style="..." and presentation attributes alike
                return False
            if tag in ("set", "animate", "animatemotion", "animatetransform") and attr == "attributename"                     and (_local(value.strip()).endswith("href") or _local(value.strip()).startswith("on")):
                return False
    return True


def _icon_data(folder: Path, icon) -> str | None:
    import base64
    from werkzeug.security import safe_join
    from ...packages import fsutil
    if not isinstance(icon, str):
        return None
    name = icon.strip()                    # trimmed first: "pot.svg " has the suffix .svg
    if not name or len(name) > _ICON_NAME_MAX:
        return None
    want = _ICON_TYPES.get(Path(name).suffix.lower())
    joined = safe_join(str(folder), name)
    if want is None or joined is None:
        return None
    try:
        root = Path(folder).resolve(strict=True)
        rel = Path(joined).relative_to(Path(folder))
        here = Path(folder)
        if fsutil.is_link(here):
            return None
        for part in rel.parts:             # no link or junction anywhere on the path, the last part too
            here = here / part
            if fsutil.is_link(here):
                return None
        path = here.resolve(strict=True)
        if path == root or not path.is_relative_to(root) or not path.is_file():
            return None
        with open(path, "rb") as fh:
            data = fh.read(_ICON_MAX + 1)  # never more than the cap, whatever the file's size says
    except (OSError, ValueError, RuntimeError):
        return None
    if not data or len(data) > _ICON_MAX:
        return None
    if _icon_kind(data) != want:           # the bytes decide; a name that disagrees is refused
        return None
    return f"data:{want};base64,{base64.b64encode(data).decode()}"


@route(bp, "DELETE", "/v1/packages/<id>", access=OWNER)
def packages_remove(id):
    keep = request.args.get("keep_data") or None
    try:
        result = current_core().packages.remove(id, keep_data=keep)
    except AddError as exc:
        return _problem(exc)
    return jsonify(result), (200 if result.get("removed") else 409)


@route(bp, "POST", "/v1/packages/<id>/start", access=OWNER)
def packages_start(id):
    _row, missing = _row_or_404(id)
    if missing:
        return missing
    try:
        return jsonify(current_core().steward.start(id))
    except StewardError as exc:
        return jsonify(error=str(exc)), 409


@route(bp, "GET", "/v1/packages/sendable", access=OWNER)
def packages_sendable():
    """The installed packages that take files (D127; 02 ``accepts``)."""
    from ... import send_file
    return jsonify(send_file.sendable(current_core()))


@route(bp, "POST", "/v1/packages/<id>/send", access=OWNER_DEVICE)
def packages_send(id: str):
    """One file to one package (D127): ``{file_name, content_b64}`` from her picker, or ``{path}`` for a document on her
    list or a file in a folder she linked, and no other path. The same review as any package tool call."""
    import base64
    import binascii
    from ... import send_file
    from ...zone1.archivist import for_state
    from ...zone1.folders import Library
    core = current_core()
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    try:
        if isinstance(body.get("path"), str):
            lib = Library(for_state(core.state, core.bus), core.state)
            path = lib.handable(body["path"].strip().strip('"'))
            if path is None:
                return jsonify(error="Only a document on your list or a file in a folder you linked can be sent this way."), 400
            if path.stat().st_size > send_file.MAX_SEND:
                raise send_file.SendRefused("That file is too big to send.")
            name, data = path.name, path.read_bytes()
        else:
            name, raw = body.get("file_name"), body.get("content_b64")
            if not isinstance(name, str) or not name.strip() or not isinstance(raw, str):
                return jsonify(error='Send {"file_name": "...", "content_b64": "..."} or {"path": "..."}.'), 400
            if len(raw) > send_file.MAX_SEND * 4 // 3 + 16:
                raise send_file.SendRefused("That file is too big to send.")
            name, data = name, base64.b64decode(raw, validate=True)
        line = send_file.hand(core, current_app.extensions.get("puffpai.tool_review"), id, name, data)
    except send_file.SendRefused as exc:
        return jsonify(error=str(exc)), 409
    except (binascii.Error, ValueError):
        return jsonify(error="content_b64 is not base64."), 400
    except OSError:
        return jsonify(error="That file can't be read right now."), 409
    return jsonify(ok=True, line=line)


@route(bp, "POST", "/v1/packages/<id>/stop", access=OWNER)
def packages_stop(id):
    _row, missing = _row_or_404(id)
    if missing:
        return missing
    core = current_core()
    clean = core.steward.stop(id)
    return jsonify(row=core.registry.get(id), nothing_running=clean)


@route(bp, "POST", "/v1/packages/<id>/update", access=OWNER_DEVICE)
def packages_update(id):
    """A built-in (lane I13): compared with this BuildBot's own copy. Up to date: 200 ``current``. A system
    part: updated now, 200 ``updated``. An app: its Yes card, 202 ``card``. Any other package: fetched again
    from where it came from, its Yes card (202). The owner at a paired device only (an API key can't)."""
    pm = current_core().packages
    try:
        if pm.builtin_of(id) is not None:
            got = pm.update_builtin(id, notices=getattr(current_core(), "notices", None))
            return jsonify(got), (202 if got.get("status") == "card" else 200)
        return jsonify({**pm.update(id), "status": "card"}), 202
    except AddError as exc:
        return _problem(exc)


def _page_link(id: str, where: str) -> str:
    """A package page on its own origin (D53), ``http://<id>.localhost:8422``; never the Core's own address."""
    from ..app import package_origin
    where = where if where.startswith("/") else "/" + where
    if _on_network():                                 # D130: its own port on the address this device uses, or nothing
        origin = current_app.extensions[EXTENSION].page_origin(request.host, id)
        return origin + where if origin else ""
    return package_origin(id, current_app.config["PUFFPAI_PAGES_ORIGIN"]) + where


def _on_network() -> bool:
    return request.environ.get("puffpai.listener") == NETWORK_LISTENER


def _opens_on_computer(url: str) -> bool:
    """D130: an address that means this computer. On a phone it would mean the phone itself."""
    try:
        host = (urllib.parse.urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return True
    return host in ("127.0.0.1", "localhost", "::1") or host.endswith(".localhost")


@route(bp, "POST", "/v1/packages/<id>/open/<page>", access=OWNER)
def packages_open(id, page):
    row, missing = _row_or_404(id)
    if missing:
        return missing
    ui = row.get("ui") or {}
    on_computer = jsonify(error=f"{row['name']}: {ON_COMPUTER}", code="this_computer"), 409   # D130
    if _on_network() and adopted_origin(row.get("origin")):
        return on_computer
    for p in ui.get("pages", []):
        if p.get("id") == page:
            if "url" in p:
                if _on_network() and _opens_on_computer(p["url"]):
                    return on_computer
                return jsonify(open="new_tab", url=p["url"])
            where = p.get("path") or "/" + p.get("file", "")
            from ...zone2.apps import puffpai_own
            show = p.get("show", "in_desktop") if puffpai_own(row) else "new_tab"     # third party: its own tab (D83)
            return jsonify(open=show, url=_page_link(id, where))
    for w in ui.get("widgets", []):
        if w.get("id") == page:
            return jsonify(open="widget", url=_page_link(id, w.get("path") or "/" + w.get("file", "")))
    program = ui.get("open")
    if program and page in ("program", "open"):
        if _on_network():
            return on_computer                          # a program would open on this computer's screen, not hers
        core = current_core()
        manifest = core.steward.read_manifest(id)       # the Core's one reader (her Yes, by its sha256)
        if manifest is None:
            return jsonify(error=f"{row['name']} was not opened: its settings file can't be used. "
                                 "Add it again."), 409
        # The program is the one in the file her Yes covered (the row's copy must agree with it).
        program = (manifest.get("ui") or {}).get("open")
        if not program or program != ui.get("open"):
            return jsonify(error=f"{row['name']} was not opened: its settings file does not match "
                                 "its record. Add it again."), 409
        folder = core.steward.folder(id)
        values = core.steward.values(id, folder, run_block(manifest) or {}, row.get("port"))
        argv = [fill(program["program"], values)] + [fill(a, values) for a in program.get("args", [])]
        try:
            # The one on-purpose visible window (02 "No console windows").
            subprocess.Popen(argv, cwd=str(folder), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as exc:
            return jsonify(error=f"{row['name']} could not be opened: {exc.strerror or exc}."), 409
        return jsonify(open="program", program=Path(argv[0]).name)
    return jsonify(error=f"{row['name']} has no page {page!r}."), 404


def _core_ports(core) -> set[int]:
    """Every port the Core itself answers on: its own address, the pages address and the Bridge proxy
    (the one it really got, when it was started on any free port), and always 8420 to 8422. An address
    there is never handed out as an app's page."""
    from ..app import pages_port_of
    ports = {getattr(core, "port", None), getattr(core, "pages_port", None), getattr(core, "proxy_port", None),
             pages_port_of(current_app.config["PUFFPAI_PAGES_ORIGIN"]), 8420, 8421, 8422}
    for origin in current_app.config["PUFFPAI_CORE_ORIGINS"]:
        ports.add(pages_port_of(origin))
    proxy = getattr(getattr(core, "zone3", None), "proxy", None)
    ports.add(getattr(proxy, "port", None))
    net = current_app.extensions[EXTENSION]           # D130: the network's ports are the Core's too
    ports.add(net.port)
    ports.update(net.page_range)
    host_port = request.host.rpartition(":")[2]
    if host_port.isdigit():
        ports.add(int(host_port))                       # the address this request came in on
    return {int(p) for p in ports if isinstance(p, int) and p > 0}


@route(bp, "GET", "/v1/packages/<id>/frame", access=OWNER)
def packages_frame(id):
    """D82, D83: may this package's page be shown inside a Home tile or the window's side pane (only a page of
    PuffPAI's own origin, on its own address), else its address for the Open button (``apps.frame_view``)."""
    from ...zone2 import apps
    row, missing = _row_or_404(id)
    if missing:
        return missing
    try:
        view = apps.frame_view(row, core_ports=_core_ports(current_core()),
                               pages_link=lambda where: _page_link(id, where))
        if _on_network() and (adopted_origin(row.get("origin")) or not view.get("url")
                              or _opens_on_computer(view["url"])):
            name = row.get("name") or id                # D130: never a link to the phone's own 127.0.0.1
            return jsonify(dict(view, ok=False, url="", kind="computer", sandbox="", line=f"{name}: {ON_COMPUTER}"))
        return jsonify(view)
    except Exception:  # noqa: BLE001 - never a 500: the tile falls back to a new tab
        import logging
        logging.getLogger(__name__).exception("frame view for %s", id)
        name = row.get("name") or id
        return jsonify(ok=False, url="", kind="none", sandbox="", running=row.get("status") == "running",
                       line=f"{name}'s page could not be checked just now. Try again.")


@route(bp, "GET", "/v1/packages/<id>/logs", access=OWNER)
def packages_logs(id):
    path = current_core().steward.log_path(id)
    if not path.is_file():
        return Response("", mimetype="text/plain")
    try:
        lines = max(1, min(int(request.args.get("lines", 200)), 5000))
    except ValueError:
        lines = 200
    text = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    return Response("\n".join(text) + "\n", mimetype="text/plain")
