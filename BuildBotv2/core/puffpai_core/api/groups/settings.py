"""Core API group `settings` (lane L5): the active Desktop, the avatar, the Bridge posture.

The Desktop and avatar are found by kind (Registry rows, or the packages that
ship with PuffPAI); nothing here knows one package by name except the
default Desktop's id. The Bridge posture is the Bridge's own (lane L4's policy, the one source that
``GET /v1/bridge`` and ``puffpai bridge status`` read too, lane I3b, through ``Bridge.view``): ``bridge``
is ``open`` only when something can actually go out, else ``closed``, and ``bridge_search`` is the wizard's
step 4 answer (Toolsmith may search for apps and knowledge while it builds, in the places it names: its switch on with her Yes behind it). Zone 3 not there: ``not_set`` (nothing leaves). ``pages`` is the pages address
(D49) the Desktop frames package pages from; avatar and icon paths (``/pkg/...``)
are on that address. ``bot`` is the bot's own name and look (lane I10c, ``puffpai_core.bot``):
``PUT /v1/settings/bot`` (a paired device only) and the wizard's two steps save through the same code, and
``GET /v1/settings/themes`` lists the looks: the built-in ones and the installed look packages (D76).
``opens`` is what opens when BuildBot starts (D94, ``puffpai_core.opens``): "window" (the default, D81), "chat" or
"dashboard", saved with ``PUT /v1/settings/opens``. ``POST /v1/window/open`` goes back to the window from the dashboard
(``puffpai_core.window``): one window per bot, for a paired-device token (her paired browsers, and this computer's
terminal key, which is one by design). ``POST /v1/terminal/open`` is the window's Terminal button (D115): the real
terminal chat in a console of its own, for the same tokens.
"""

from flask import Blueprint, current_app, g, jsonify, request

from ... import bot, desktop, jsonfile, layout, opens
from ..auth import OWNER, OWNER_DEVICE
from ..network import NETWORK_LISTENER
from ..routing import current_core, route

bp = Blueprint("settings", __name__)


def _bridge() -> tuple[str, bool, list]:
    """``(open | closed | not_set, search on, the posture in her words)`` from the Bridge's own policy (the
    one source; ``zone3.bridge.plain`` words it the same as ``puffpai bridge status``)."""
    try:
        from ...zone3.service import for_app
        bridge = for_app(current_app).bridge
    except Exception:  # noqa: BLE001 - no Zone 3 here: nothing leaves, and nothing is claimed
        return "not_set", False, []
    core = current_core()
    view = bridge.view({r["id"]: r["name"] for r in core.registry.list()})    # the same reading as /v1/bridge
    return ("open" if view.get("state") == "open" else "closed"), bool(view.get("search")), list(view["said"])


def _view():
    core = current_core()
    wizard = jsonfile.read(core.state / "settings" / "wizard.json", {})
    bridge, search, said = _bridge()
    on_network = request.environ.get("puffpai.listener") == NETWORK_LISTENER     # D130: no 127.0.0.1 pages address there
    return {"ui": desktop.active_ui(core.state, core.registry), "own_desktop": desktop.own_desktop_is_active(core.state),
            "uis": desktop.ui_ids(core.registry),
            "avatar": desktop.avatar_view(core.state, core.registry),
            "bridge": bridge, "bridge_search": search, "bridge_said": said,
            "wizard_complete": bool(wizard.get("complete")), "bot": bot.read(core.state, core.registry),
            "opens": opens.read(core.state), "pages": "" if on_network else current_app.config["PUFFPAI_PAGES_ORIGIN"],
            **({"network": True} if on_network else {})}


@route(bp, "GET", "/v1/settings", access=OWNER)
def settings_get():
    return jsonify(_view())


@route(bp, "PUT", "/v1/settings/ui", access=OWNER)
def settings_ui():
    body = request.get_json(silent=True) or {}
    core = current_core()
    try:
        desktop.set_ui(core.state, core.registry, body.get("id") if isinstance(body, dict) else None)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(_view())


@route(bp, "PUT", "/v1/settings/avatar", access=OWNER)
def settings_avatar():
    body = request.get_json(silent=True)
    core = current_core()
    if not isinstance(body, dict) or "id" not in body:
        return jsonify(error="Send {\"id\": \"<avatar package id>\"}, or {\"id\": null} for none."), 400
    try:
        desktop.set_avatar(core.state, core.registry, body["id"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(_view())


@route(bp, "GET", "/v1/settings/themes", access=OWNER)
def settings_themes():
    core = current_core()
    return jsonify(bot.themes(core.state, core.registry))


@route(bp, "PUT", "/v1/settings/bot", access=OWNER_DEVICE)
def settings_bot():
    body = request.get_json(silent=True)
    if (not isinstance(body, dict) or not ({"name", "theme"} & set(body)) or set(body) - {"name", "theme"}
            or any(v is None for v in body.values())):          # a null is no name, not "leave it"
        return jsonify(error='Send {"name": "<1 to 40 characters>", "theme": "<a look id>"}, one or both.'), 400
    try:
        core = current_core()
        bot.save(core.state, name=body.get("name"), theme=body.get("theme"),
                 by=getattr(g.caller, "label", "") or "owner", registry=core.registry)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(_view())


@route(bp, "GET", "/v1/settings/drawbridge", access=OWNER)
def settings_drawbridge_get():
    from ...zone3 import files_mode
    return jsonify(files_mode.view(current_core().state))


#: Who may change it: the owner at a paired device, never the terminal key this computer's CLI uses (any program
#: running as her can read that key; ``groups.dev`` refuses it for the same reason). Not imported from api.app.
TERMINAL_PREFIX = "this computer's terminal"
DRAWBRIDGE_REFUSED = ("Only you can change this, from the Developer page in a browser you paired. The terminal "
                      "key this computer's CLI uses can't change it.")
DRAWBRIDGE_NO_AUDIT = "BuildBot could not write the audit line, so the setting was not changed."


@route(bp, "PUT", "/v1/settings/drawbridge", access=OWNER_DEVICE)
def settings_drawbridge_put():
    """D95: the owner's one setting for files Drawbridge can't read. One audit line per change (the two
    setting names only); no event carries it."""
    from ...zone3 import files_mode
    from ...zone3.service import for_app
    caller = g.caller
    if caller is None or caller.via != "device" or str(caller.label).startswith(TERMINAL_PREFIX):
        return jsonify(error=DRAWBRIDGE_REFUSED), 403
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"files"} or body["files"] not in files_mode.MODES:
        return jsonify(error=files_mode.ASK), 400
    state = current_core().state
    before, after = files_mode.read(state), body["files"]
    if before != after:
        files_mode.save(state, after)
        try:
            for_app(current_app).audit.append({"actor": "owner", "action": "drawbridge_files", "from": before,
                                               "to": after})
        except Exception:  # noqa: BLE001 - no audit line: the change is undone
            files_mode.save(state, before)
            return jsonify(error=DRAWBRIDGE_NO_AUDIT), 503
    return jsonify(files_mode.view(state))


@route(bp, "GET", "/v1/settings/edits", access=OWNER)
def settings_edits():
    """D140: may the bot change her documents? Off until she turns it on (``owner_files``)."""
    from ... import owner_files
    return jsonify(on=owner_files.edits_on(current_core().state))


EDITS_NO_AUDIT = "BuildBot could not write the audit line, so changing your documents stays off."


@route(bp, "PUT", "/v1/settings/edits", access=OWNER_DEVICE)
def settings_edits_put():
    """It also lets the real text of a private detail go in her file (``owner_files.takes_real_text``), so one audit line
    per change (the setting only). Without that line it does not turn on; off always works, as ``PUT /v1/network``."""
    from ... import owner_files
    from ...zone3.service import for_app
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        return jsonify(error='Send {"on": true} or {"on": false}.'), 400
    state, on = current_core().state, body["on"]
    if on != owner_files.edits_on(state):
        try:
            for_app(current_app).audit.append({"actor": "owner",
                                               "action": "document_edits_on" if on else "document_edits_off"})
        except Exception:  # noqa: BLE001 - no audit line: off stays off, and off still goes off
            if on:
                return jsonify(error=EDITS_NO_AUDIT), 503
    return jsonify(on=owner_files.set_edits(state, on))
def _moved(fn, *args):
    from ...bot_move import MoveRefused
    try:
        return jsonify(fn(*args))
    except MoveRefused as exc:
        return jsonify(error=str(exc)), 400


def _move_folder():
    body = request.get_json(silent=True)
    raw = body.get("path") if isinstance(body, dict) else None
    return raw.strip().strip('"') if isinstance(raw, str) and raw.strip() and len(raw) < 1000 and "\x00" not in raw else None


@route(bp, "GET", "/v1/settings/move", access=OWNER)
def settings_move():
    """D141: what doesn't travel, said plainly before she does anything."""
    from ... import bot_move
    return jsonify(stays=list(bot_move.STAYS))


@route(bp, "POST", "/v1/settings/move/export", access=OWNER_DEVICE)
def settings_move_export():
    from ... import bot_move
    folder = _move_folder()
    if folder is None:
        return jsonify(error="Say which folder."), 400
    core = current_core()
    return _moved(bot_move.export, core.state, core.registry, core.packages, folder)


@route(bp, "POST", "/v1/settings/move/check", access=OWNER_DEVICE)
def settings_move_check():
    """Check a moved bot's every hash and say what it holds. Nothing changes."""
    from ... import bot_move
    folder = _move_folder()
    if folder is None:
        return jsonify(error="Say which folder."), 400
    return _moved(lambda: bot_move.lines_for(bot_move.read(folder)))


@route(bp, "POST", "/v1/settings/move/import", access=OWNER_DEVICE)
def settings_move_import():
    """Her name, look, who the bot is and Home. Each app is then added the normal way, with its own Yes:
    ``POST /v1/packages`` ``{source}``, from ``apps[].source`` as this answers it."""
    from ... import bot_move
    folder = _move_folder()
    if folder is None:
        return jsonify(error="Say which folder."), 400
    core = current_core()

    def go() -> dict:
        record = bot_move.read(folder)
        said = bot_move.apply(core.state, record, core.registry)
        out = bot_move.lines_for(record)
        for a, raw in zip(out["apps"], bot_move.to_add(record)):
            a["source"] = bot_move.app_source(record, raw)
        return {**out, "said": said}
    return _moved(go)


@route(bp, "PUT", "/v1/settings/opens", access=OWNER)
def settings_opens():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or set(body) != {"opens"}:
        return jsonify(error='Send {"opens": "window"}, {"opens": "chat"} or {"opens": "dashboard"}.'), 400
    try:
        opens.save(current_core().state, body["opens"])
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(_view())


@route(bp, "POST", "/v1/window/open", access=OWNER_DEVICE)
def window_open():
    """From the dashboard, back to the window (the owner, 10-30). It runs this program's own window and takes nothing
    from the request; a second open brings the first one forward, and one that is starting is left to start. A window
    that can't open here is ok false with its plain line, not an error."""
    from ... import window
    if request.environ.get("puffpai.listener") == NETWORK_LISTENER:   # D130: this browser stands in for the window
        return jsonify(ok=True, url="/window/", line="The window opens in this browser.")
    core = current_core()
    port = int(current_app.config["PUFFPAI_DESKTOP_HOST"].rsplit(":", 1)[1])
    ok, line = window.open_window(port, core.state)
    return jsonify(ok=ok, line=line)


TERMINAL_SAFE_MODE = "BuildBot is in safe mode, so the terminal chat is closed until you restart BuildBot."


@route(bp, "POST", "/v1/terminal/open", access=OWNER_DEVICE)
def terminal_open():
    """From the window's switch (D115): the real terminal chat, in a console of its own, in the chat she is in. It takes
    that chat's id and nothing else, by the store's own rule: never a command, a path or a folder. A console that can't
    open here is ok false with its plain line, not an error. Refused in safe mode, as ``/v1/dev/shell`` is."""
    from ... import cli
    from ...zone2 import conversations
    if request.environ.get("puffpai.listener") == NETWORK_LISTENER:   # D130: no console on a screen she can't see
        return jsonify(ok=False, url="/#developer/terminal", line="A console would open on the bot's computer, so use the "
                                                                  "Developer page's command line here.")
    try:
        safe = bool(current_core().caretaker.in_safe_mode)
    except Exception:  # noqa: BLE001 - can't tell: closed
        safe = True
    if safe:
        return jsonify(error=TERMINAL_SAFE_MODE, code="safe_mode"), 409
    body = request.get_json(silent=True)
    conversation = body.get("conversation", conversations.DEFAULT) if isinstance(body, dict) else conversations.DEFAULT
    if not conversations.valid_id(conversation):
        return jsonify(error="That is not a chat's id."), 400
    ok, line = cli.open_terminal(current_core().state, conversation)
    if line == cli.TERMINAL_NOT_HERE:                                  # D144: the window off Windows
        return jsonify(ok=False, pane=True, line=line)
    return jsonify(ok=ok, line=line)


# -- Home layout (lane I8b): pin, hide, move and rename tiles; harvested from v1 dashboard_widgets.py ------

@route(bp, "GET", "/v1/settings/layout", access=OWNER)
def settings_layout_get():
    return jsonify(layout.get_layout(current_core().state))


@route(bp, "PUT", "/v1/settings/layout", access=OWNER)
def settings_layout_put():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='Send {"tiles": {...}}, {"seen": {"tiles": {...}}}, or both.'), 400
    return jsonify(layout.save_layout(current_core().state, body))


@route(bp, "POST", "/v1/settings/layout/arrange", access=OWNER)
def settings_layout_arrange():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    core, action = current_core(), body.get("action")
    if action in ("add_app", "remove_app"):           # D82: an app's own page on Home, or off it
        from ...zone2.apps import shows_inside
        row = core.registry.get(str(body.get("app") or ""))
        if row is None or row.get("status") == "removed":
            return jsonify({"ok": False, "line": "That app is not installed here."}), 400
        if action == "add_app" and not (row.get("ui") or {}).get("pages"):
            return jsonify({"ok": False, "line": f"{row.get('name')} has no page to show on Home. "
                                                 "It is in Your apps, where it opens."}), 400
        got = layout.app_page(core.state, row["id"], row.get("name"), action == "add_app", inside=shows_inside(row))
    elif action == "apps_as":
        got = layout.apps_view(core.state, body.get("view"))
    else:
        got = layout.arrange(core.state, action, body.get("tile"), position=body.get("position"),
                             name=body.get("name") or "")
    return jsonify(got), (200 if got["ok"] else 400)
