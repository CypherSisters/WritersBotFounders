"""Core API group `dev` (lane i8c): the Developer page's command line, a FULL shell on the owner's computer.

The owner's rule (2026-10-06): no Yes before each command. It is gated by her owner login on a
paired device (``owner_device``: never an owner API key, a package token or a Toolsmith token; and never
the terminal key the Core writes for its own CLI, which any program running as her can read), the idle
timeout, the open cap and audit lines that never hold the command text. Refused in safe mode (open,
input, events); closing and listing always work. Entering safe mode closes every shell. What runs here is
not checked by the Bridge; the Desktop says so. Chat's own command running keeps its per-command Yes.

A shell is bound to the device that opened it: input and events from another device get 404, as for an
id that does not exist. Any of her paired devices may list them and close one (closing only narrows).
The shells themselves: ``puffpai_core.devshell``.
"""

from __future__ import annotations

import hashlib
import json
import time

from flask import Blueprint, Response, current_app, g, jsonify, request, stream_with_context

from ... import devshell
from ...steward import noshell
from ..auth import OWNER_DEVICE
from ..routing import route

bp = Blueprint("dev", __name__)
EXT = "puffpai.devshell"
HEARTBEAT_S = 10
TERMINAL_PREFIX = "this computer's terminal"          # api.app.TERMINAL_LABEL (not imported: app imports us)

NOT_HERE = "No such command line. It may have closed."
TERMINAL_REFUSED = ("The command line opens only for you at a paired device. The terminal key this "
                    "computer's CLI uses can't open it.")
SAFE_MODE = "BuildBot is in safe mode, so the command line is closed until you restart BuildBot."
NO_AUDIT = "BuildBot could not write the audit line, so nothing was {done}."
ONE_LINE = "Send one line at a time, at most {n} characters, with no line breaks."
NOT_CHECKED = "What you run here is not checked by the Bridge."


def shells(app=None) -> devshell.Shells:
    return (app or current_app).extensions[EXT]


def init_app(app) -> None:
    def audit(record: dict) -> None:
        from ...zone3.service import for_app
        for_app(app).audit.append(record)

    def tokens() -> tuple[str, ...]:
        key = app.extensions.get("puffpai.started")
        return (key,) if isinstance(key, str) else ()

    # Safe mode closes it through the shared step (``caretaker.safe_mode_steps``: ``devshell.close_every``).
    app.extensions[EXT] = devshell.Shells(audit=audit, tokens=tokens)


def close_all(app, why: str = "core_stop") -> int:
    mgr = app.extensions.get(EXT)
    return mgr.close_all(why) if mgr is not None else 0


def _owner() -> str:
    """Which login asks: the hash of its device token (a shell belongs to the device that opened it). The token
    the gate signed in with (D79), never simply the first cookie, which another page may have planted."""
    token = g.get("token")
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _refused(safe_too: bool = True):
    caller = g.caller
    if caller is None or caller.via != "device" or str(caller.label).startswith(TERMINAL_PREFIX):
        return jsonify(error=TERMINAL_REFUSED), 403
    if safe_too:
        try:
            safe = bool(current_app.extensions["puffpai"].caretaker.in_safe_mode)
        except Exception:  # noqa: BLE001 - can't tell: closed
            safe = True
        if safe:
            return jsonify(error=SAFE_MODE, code="safe_mode"), 409
    return None


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@route(bp, "POST", "/v1/dev/shell", access=OWNER_DEVICE)
def dev_shell_open():
    refused = _refused()
    if refused:
        return refused
    kind = _body().get("shell", devshell.DEFAULT_SHELL)
    if kind not in devshell.SHELLS:
        return jsonify(error=f'The shell is "{devshell.SHELLS[0]}" or "{devshell.SHELLS[1]}".'), 400
    try:
        sh = shells().open(kind, _owner(), str(g.caller.label))
    except devshell.TooMany as exc:
        return jsonify(error=str(exc), code="too_many"), 429
    except (devshell.StartFailed, noshell.NoShell) as exc:
        return jsonify(error=str(exc.args[0] if exc.args else exc)), 503
    except OSError:
        return jsonify(error=devshell.NO_SHELL.format(shell=kind)), 503
    except Exception:  # noqa: BLE001 - the audit line could not be written: no shell
        return jsonify(error=NO_AUDIT.format(done="opened")), 503
    return jsonify(id=sh.id, cwd=sh.cwd), 201


@route(bp, "GET", "/v1/dev/shell", access=OWNER_DEVICE)
def dev_shell_list():
    refused = _refused(safe_too=False)
    if refused:
        return refused
    mgr = shells()
    mgr.reap()
    me = _owner()
    rows = []
    for row in mgr.list():
        sh = mgr.get(row["id"])
        rows.append(dict(row, mine=bool(sh is not None and sh.owner == me)))
    return jsonify(rows)


@route(bp, "POST", "/v1/dev/shell/<id>/input", access=OWNER_DEVICE)
def dev_shell_input(id):
    refused = _refused()
    if refused:
        return refused
    mgr = shells()
    mgr.reap()
    sh = mgr.get(id, _owner())
    if sh is None or not sh.open:
        return jsonify(error=NOT_HERE), 404
    body = _body()
    if body.get("signal") is not None:
        if body.get("signal") != "ctrl_c" or "text" in body:
            return jsonify(error='The one signal is "ctrl_c".'), 400
        try:
            return jsonify(ok=True, result=mgr.ctrl_c(sh))
        except (devshell.StartFailed, OSError):
            return jsonify(error=NOT_HERE), 404
    text = body.get("text")
    if not isinstance(text, str) or len(text) > devshell.MAX_LINE or any(c in text for c in "\r\n\x00"):
        return jsonify(error=ONE_LINE.format(n=devshell.MAX_LINE)), 400
    try:
        new = mgr.line(sh, text)
    except (devshell.StartFailed, OSError, ValueError):
        return jsonify(error=NOT_HERE), 404
    except Exception:  # noqa: BLE001 - no audit line: nothing sent
        return jsonify(error=NO_AUDIT.format(done="sent")), 503
    return jsonify(ok=True, kind="line" if new else "typed")


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@route(bp, "GET", "/v1/dev/shell/<id>/events", access=OWNER_DEVICE)
def dev_shell_events(id):
    refused = _refused()
    if refused:
        return refused
    mgr = shells()
    sh = mgr.get(id, _owner())
    if sh is None:
        return jsonify(error=NOT_HERE), 404
    after = max(0, _int(request.headers.get("Last-Event-ID", request.args.get("after")), 0))
    timeout = max(0, min(60, _int(request.args.get("timeout"), 25)))
    began = mgr.stopping

    def frames():
        nonlocal after
        end = time.monotonic() + timeout
        yield "retry: 2000\n\n"
        while True:
            for seq, ev in sh.since(after):
                after = seq
                yield f"id: {seq}\ndata: {json.dumps(ev, separators=(',', ':'))}\n\n"
                if "exit" in ev:
                    return
            if mgr.stopping != began:
                return
            left = end - time.monotonic()
            if left <= 0:
                return
            if not sh.wait(after, min(left, HEARTBEAT_S)):
                yield ": still here\n\n"

    return Response(stream_with_context(frames()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@route(bp, "DELETE", "/v1/dev/shell/<id>", access=OWNER_DEVICE)
def dev_shell_close(id):
    refused = _refused(safe_too=False)
    if refused:
        return refused
    if not shells().close(id, "closed"):
        return jsonify(error=NOT_HERE), 404
    return jsonify(ok=True)
