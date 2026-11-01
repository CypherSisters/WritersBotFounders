"""Notices: a package asks the Core to tell the owner something (``POST /v1/notify``).

Kept in ``notices.json`` (the newest 200). The Desktop shows the ones not
dismissed as cards. Each post publishes ``notify.posted`` with ids only.
Sound and speech are kept for UIs that can use them; voice is not in this
week's build.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import jsonfile

KEEP = 200
URGENCY = ("low", "normal", "high")


class NoticeError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Notices:
    def __init__(self, path: Path | str, bus: Any = None) -> None:
        self.path = Path(path)
        self.bus = bus

    def _load(self) -> list[dict]:
        return jsonfile.read(self.path, [])

    def post(self, package: str, body: Any) -> dict:
        if not isinstance(body, dict):
            raise NoticeError("Send one JSON object: {title, text, sound, speak, urgency}.")
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            raise NoticeError("A notice needs a title.")
        if len(title) > 120:
            raise NoticeError("A notice title is at most 120 characters.")
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > 600:
            raise NoticeError("A notice's text is at most 600 characters of plain text.")
        urgency = body.get("urgency", "normal")
        if urgency not in URGENCY:
            raise NoticeError("urgency is low, normal or high.")
        notice = {"id": uuid.uuid4().hex[:12], "package": package, "title": title.strip(), "text": text.strip(),
                  "urgency": urgency, "sound": bool(body.get("sound", False)), "speak": bool(body.get("speak", False)),
                  "at": _now(), "dismissed": False}
        with jsonfile.LOCK:
            items = self._load()
            items.append(notice)
            jsonfile.write(self.path, items[-KEEP:])
        if self.bus is not None:
            from .bus import BUS_SOURCE
            self.bus.publish(self.bus.message("notify.posted", {"notice": notice["id"], "package": package,
                                                                "urgency": urgency}, BUS_SOURCE))
        return notice

    def list(self) -> list[dict]:
        return [n for n in reversed(self._load()) if not n.get("dismissed")]

    def dismiss(self, notice_id: str) -> bool:
        with jsonfile.LOCK:
            items = self._load()
            for n in items:
                if n["id"] == notice_id and not n.get("dismissed"):
                    n["dismissed"] = True
                    jsonfile.write(self.path, items)
                    return True
        return False
