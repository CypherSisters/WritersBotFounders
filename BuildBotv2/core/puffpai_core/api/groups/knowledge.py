"""Core API group ``knowledge`` (lane I11): what Toolsmith's builds read into the bot's knowledge.

- ``GET /v1/knowledge/sources`` (her paired device): per item fetched, its source, title, address,
  license, size and date (``zone1.fetched.sources``), newest first, with the total size. Read from the data
  folder only; nothing here reaches the network. The Desktop's Knowledge page shows it (a later lane).
- Where her books and downloads are kept (D103, ``zone1.knowledge_store``): ``GET /v1/knowledge/place`` (where, how
  much room, how a move goes, the plain line when the drive is gone or full); ``POST /v1/knowledge/place``
  ``{path, leave_old?}`` moves them there, in the background, with ``{"path": ""}`` back to the data folder;
  ``POST /v1/knowledge/place/resume`` and ``POST /v1/knowledge/place/cancel``. Every folder goes through
  ``knowledge_folder.problem`` first. Nothing here reaches the network, or touches the vault.
- Documents pinned to this bot (D125, ``zone1.pinned``): ``GET /v1/knowledge/pinned``; ``POST /v1/knowledge/pinned``
  ``{path}`` pins one file and reads it through the gate now; ``POST /v1/knowledge/pinned/remove`` ``{path}`` unpins it.
  Every file goes through ``folders.file_problem`` first. The file itself is only ever read.
- ``POST /v1/knowledge/pick`` ``{kind: file | folder}`` (D127): Windows's own Open dialog on this computer. From another
  device, or where there is no such dialog, it says so, and a file comes by upload instead (D134).
- ``POST /v1/knowledge/upload?name=<file name>`` (D134, ``zone1.uploads``): the file's bytes as the body, from the
  browser's own file input. It lands in the data folder's ``uploads`` folder; the name is the only thing the client says.
- Her books (D136, ``zone1.books``): ``POST /v1/knowledge/find`` ``{words}``, a plan for books only (D99's card, no apps
  looked for, no model asked); ``POST /v1/knowledge/books/remove`` ``{source, file?}``; ``GET /v1/knowledge/books/read``
  ``?source&file&part``; ``GET /v1/knowledge/books/articles`` ``?source&q&page``; ``GET /v1/knowledge/books/article``
  ``?source&url``; ``POST /v1/knowledge/books/look`` looks for a dropped ``.zim`` now. ``GET /v1/knowledge/sources`` also
  looks, and says each library and where to drop one.
- Her files (D136, ``owner_files``): ``GET /v1/knowledge/files``, the newest in My files; ``POST /v1/knowledge/files/open``
  ``{path}`` opens one in its own app on the bot's computer. ``GET /v1/owner-files`` ``[?path=]``, for a package with
  ``needs.owner_files`` and its own token: where it makes files, or where one file is.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, g, jsonify, request, send_file

from ... import owner_files
from ...zone1 import books, fetched, knowledge_store
from ..auth import OWNER, OWNER_DEVICE, PACKAGE_OK
from ..network import NETWORK_LISTENER, NETWORK_PAGE_LISTENER
from ..routing import current_core, route

bp = Blueprint("knowledge", __name__)
MAX_PATH = 1000


def _refused(exc: knowledge_store.MoveRefused):
    status = 409 if exc.code in ("running", "unfinished", "source_away", "switched", "stuck", "none", "downloading") else 400
    r = jsonify(error=str(exc), code=exc.code)
    r.status_code = status
    return r


def _downloading() -> bool:
    try:
        from .toolsmith import service
        builds = service().store.all()
    except Exception:  # noqa: BLE001 - no Toolsmith here: no download either
        return False
    return any(isinstance(b, dict) and (b.get("knowledge_run") or {}).get("state") == "fetching" for b in builds)


def _no_downloads_now() -> None:
    """A download running now would write to the old place behind the move: she moves after it ends."""
    if _downloading():
        raise knowledge_store.MoveRefused("Toolsmith is downloading right now. Move them when that ends.", "downloading")
    if books.learning(current_core().state):
        raise knowledge_store.MoveRefused("Your bot is learning a library you added. Move them when that ends.", "downloading")


def _library():
    from ...zone1.archivist import for_state
    from ...zone1.folders import Library
    core = current_core()
    return Library(for_state(core.state, core.bus), core.state)


def _pinned(status: int = 200):
    r = jsonify(pinned=_library().pinned_view(),
                line="Your bot reads these in every chat, where they are. It never copies, moves or changes them.")
    r.status_code = status
    r.headers["Cache-Control"] = "no-store"
    return r


def _file_arg() -> str | None:
    body = request.get_json(silent=True)
    raw = body.get("path") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_PATH or "\x00" in raw:
        return None
    return raw.strip().strip('"')


@route(bp, "POST", "/v1/knowledge/pick", access=OWNER_DEVICE)
def knowledge_pick():
    """Windows's own Open dialog, on this computer (D127), so she never types a path. It answers a path and changes
    nothing: Pin and Link check it as they check a typed one. From another device there is no dialog to see."""
    from ... import send_file
    body = request.get_json(silent=True)
    kind = body.get("kind") if isinstance(body, dict) else None
    if kind not in ("file", "folder"):
        return jsonify(error='Send {"kind": "file"} or {"kind": "folder"}.'), 400
    # D130: which listener, set by the server. Never the peer's address alone: being local is no proof on the network.
    if request.environ.get("puffpai.listener") in (NETWORK_LISTENER, NETWORK_PAGE_LISTENER) \
            or request.remote_addr not in ("127.0.0.1", "::1") or not send_file.can_pick():
        if kind == "file":                               # D134: the browser's own file input, then an upload
            return jsonify(path="", typed=True, upload=True, line="Choose the file from this device.")
        return jsonify(path="", typed=True, line=FOLDER_TYPED)
    try:
        return jsonify(path=send_file.pick(kind))
    except send_file.SendRefused as exc:
        return jsonify(error=str(exc)), 409


FOLDER_TYPED = ("A folder is read where it is, so it can't come from this device. Type the path of a folder on your bot, "
                "or add its files one at a time.")


@route(bp, "POST", "/v1/knowledge/upload", access=OWNER_DEVICE)
def knowledge_upload():
    """One file from the device she is at (D134). Pin and Send to... work on the copy; the reply names it."""
    from ...zone1 import uploads
    lib = _library()
    try:
        got = uploads.save(lib, request.args.get("name"), request.stream, request.content_length)
    except uploads.UploadRefused as exc:
        return jsonify(error=str(exc), code=exc.code), exc.status
    except OSError:
        return jsonify(error=uploads.NOT_SAVED, code="not_saved"), 507
    try:
        lib.scan_file(Path(got["path"]))                 # through the gate now, as a pin is. A second copy is read again
    except Exception:  # noqa: BLE001 - it is saved; the bot's next walk reads it
        pass
    r = jsonify(ok=True, **got)
    r.headers["Cache-Control"] = "no-store"
    return r


@route(bp, "GET", "/v1/owner-files", access=PACKAGE_OK)
def owner_files_where():
    core = current_core()
    if g.caller.kind == "package" and not ((core.steward.read_manifest(g.caller.package_id) or {}).get("needs") or {}).get("owner_files"):
        return jsonify(error="Only a package that says it works on her own files can ask this."), 403
    if "path" in request.args:
        writes = g.caller.kind == "package" and ((core.steward.read_manifest(g.caller.package_id) or {}).get("needs") or {}).get("owner_files") == "write"
        if writes and not owner_files.edits_on(core.state):   # D140: off, and the package hears it from the Core too
            return jsonify(line=owner_files.EDITS_OFF)
        return jsonify(owner_files.where(core.state, request.args["path"]))
    return jsonify(made=str(owner_files.made_folder(core.state)))


@route(bp, "GET", "/v1/knowledge/files", access=OWNER)
def knowledge_files():
    r = jsonify(files=owner_files.recent(current_core().state))
    r.headers["Cache-Control"] = "no-store"
    return r


@route(bp, "POST", "/v1/knowledge/files/open", access=OWNER_DEVICE)
def knowledge_file_open():
    text = _file_arg()
    if text is None:
        return jsonify(error="Say which file."), 400
    if request.environ.get("puffpai.listener") == NETWORK_LISTENER:   # D130: to the browser that asked, not a far screen
        path, why = owner_files.for_device(current_core().state, text)
        if why:
            return jsonify(error=why), 400
        resp = send_file(path, as_attachment=True, download_name=Path(path).name)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
    why = owner_files.open_file(current_core().state, text)
    return (jsonify(error=why), 400) if why else jsonify(opened=True)


@route(bp, "GET", "/v1/knowledge/pinned", access=OWNER)
def knowledge_pinned():
    return _pinned()


@route(bp, "POST", "/v1/knowledge/pinned", access=OWNER_DEVICE)
def knowledge_pin():
    from ...zone1.folders import FolderRefused
    text = _file_arg()
    if text is None:
        return jsonify(error=f'Send {{"path": "<a file on this computer>"}}, at most {MAX_PATH} characters.'), 400
    lib = _library()
    try:
        path = lib.pin(text)                             # the one file check runs in Library.pin
    except FolderRefused as exc:
        return jsonify(error=str(exc)), 400
    lib.scan_file(path)                                  # a line for her now; what it can't read shows on its row
    return _pinned()


@route(bp, "POST", "/v1/knowledge/pinned/remove", access=OWNER_DEVICE)
def knowledge_unpin():
    text = _file_arg()
    if text is None:
        return jsonify(error=f'Send {{"path": "<a pinned file>"}}, at most {MAX_PATH} characters.'), 400
    if not _library().unpin(text):
        return jsonify(error="That file is not pinned."), 404
    return _pinned()


@route(bp, "GET", "/v1/knowledge/place", access=OWNER)
def knowledge_place():
    return jsonify(knowledge_store.view(current_core().state))


@route(bp, "POST", "/v1/knowledge/place", access=OWNER_DEVICE)
def knowledge_place_move():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    raw = body.get("path")
    if not isinstance(raw, str) or len(raw) > MAX_PATH or "\x00" in raw:
        return jsonify(error=f'Send {{"path": "<a folder on this computer>"}}, at most {MAX_PATH} characters.'), 400
    try:
        _no_downloads_now()
        got = knowledge_store.start(current_core().state, raw, leave_old=body.get("leave_old") is True)
    except knowledge_store.MoveRefused as exc:
        return _refused(exc)
    return jsonify(got), 202


@route(bp, "POST", "/v1/knowledge/place/resume", access=OWNER_DEVICE)
def knowledge_place_resume():
    try:
        return jsonify(knowledge_store.resume(current_core().state)), 202
    except knowledge_store.MoveRefused as exc:
        return _refused(exc)


@route(bp, "POST", "/v1/knowledge/place/cancel", access=OWNER_DEVICE)
def knowledge_place_cancel():
    try:
        return jsonify(knowledge_store.cancel(current_core().state))
    except knowledge_store.MoveRefused as exc:
        return _refused(exc)


def _screen():
    """Drawbridge's text rules for a dropped library, as for a download. None here: it waits (fail closed)."""
    from .toolsmith import service
    try:
        return service().ports.screen_knowledge
    except Exception:  # noqa: BLE001 - no Toolsmith host here
        return None


@route(bp, "GET", "/v1/knowledge/sources", access=OWNER_DEVICE)
def knowledge_sources():
    core = current_core()
    state = core.state
    books.look(state, core.registry, _screen())
    items = fetched.sources(state)
    return jsonify(items=[{k: r[k] for k in ("source", "title", "url", "license", "size", "fetched_at", "file")}
                          for r in items],
                   count=len(items), bytes=sum(int(r["size"]) for r in items), libraries=books.libraries(state),
                   waiting=books.waiting(state), drop_folder=str(fetched.root(state)))


def _book(fn, *args):
    try:
        return jsonify(fn(*args))
    except books.BookRefused as exc:
        return jsonify(error=str(exc)), 400


@route(bp, "POST", "/v1/knowledge/books/remove", access=OWNER_DEVICE)
def knowledge_book_remove():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    if _downloading():
        return jsonify(error="Toolsmith is downloading right now. Remove it when that ends."), 409
    core = current_core()
    return _book(books.remove, core.state, body.get("source"), body.get("file"), core.registry)


@route(bp, "POST", "/v1/knowledge/books/look", access=OWNER_DEVICE)
def knowledge_book_look():
    core = current_core()
    return jsonify(looking=books.look(core.state, core.registry, _screen()), waiting=books.waiting(core.state))


@route(bp, "GET", "/v1/knowledge/books/read", access=OWNER_DEVICE)
def knowledge_book_read():
    a = request.args
    return _book(books.read_text, current_core().state, a.get("source"), a.get("file"), a.get("part"))


@route(bp, "GET", "/v1/knowledge/books/articles", access=OWNER_DEVICE)
def knowledge_book_articles():
    a = request.args
    return _book(books.articles, current_core().state, a.get("source"), a.get("q"), a.get("page"))


@route(bp, "GET", "/v1/knowledge/books/article", access=OWNER_DEVICE)
def knowledge_book_article():
    a = request.args
    return _book(books.article, current_core().state, a.get("source"), a.get("url"))


@route(bp, "POST", "/v1/knowledge/find", access=OWNER_DEVICE)
def knowledge_find():
    """Find more books (v1's Find more knowledge): her words go through the same screen and the same plan card as any
    build's topics, and nothing downloads before her Yes on that card (D99)."""
    from .toolsmith import Paused, service
    body = request.get_json(silent=True)
    words = " ".join(str(body.get("words") or "").split())[:60] if isinstance(body, dict) else ""
    if not words:
        return jsonify(error="Type what it should know about."), 400
    try:
        return jsonify(service().plan_books(words)), 201
    except Paused as exc:
        return jsonify(error=str(exc)), 409
