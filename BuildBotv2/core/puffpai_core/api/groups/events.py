"""Core API group `events` (lane L5): the Bus's events as server-sent events.

An owner gets what is visible to ``ui``; a package token what is visible to
``package`` (and only its own ``checklist.changed``). Each event carries its
sequence number as the SSE id, so EventSource resumes with Last-Event-ID.
The stream ends after ``timeout`` seconds (default 25, at most 60); the
browser reconnects on its own. Heartbeat comments keep the line open. When the Core
stops, every open stream ends at once (``Feed.end_streams``), so Ctrl+C never waits on one.
"""

import json

from flask import Blueprint, Response, current_app, g, request, stream_with_context

from ...events_feed import Feed
from ..auth import PACKAGE_OK
from ..routing import route

bp = Blueprint("events", __name__)
HEARTBEAT_S = 10


def init_app(app) -> None:
    app.extensions["puffpai.feed"] = Feed(app.extensions["puffpai"].bus)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@route(bp, "GET", "/v1/events", access=PACKAGE_OK)
def events_stream():
    feed: Feed = current_app.extensions["puffpai.feed"]
    caller = g.caller
    audience = "package" if caller.kind == "package" else "ui"
    package_id = caller.package_id if caller.kind == "package" else None
    after = _int(request.headers.get("Last-Event-ID", request.args.get("after")), feed.last)
    timeout = max(0, min(60, _int(request.args.get("timeout"), 25)))

    def frames():
        nonlocal after
        import time
        end = time.monotonic() + timeout
        began = feed.ended
        yield "retry: 2000\n\n"
        while True:
            if feed.ended != began:            # the Core is stopping: end now, the browser comes back later
                return
            top = feed.last                    # events this caller may not see still move it on
            for seq, msg in feed.since(after, audience, package_id):
                after = seq
                yield f"id: {seq}\nevent: {msg['type']}\ndata: {json.dumps(msg, separators=(',', ':'))}\n\n"
            after = max(after, top)
            left = end - time.monotonic()
            if left <= 0:
                return
            if not feed.wait(after, min(left, HEARTBEAT_S), began):
                yield ": still here\n\n"

    return Response(stream_with_context(frames()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
