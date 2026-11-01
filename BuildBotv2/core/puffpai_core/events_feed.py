"""The event feed behind ``GET /v1/events``: the Bus's recent messages, numbered.

One subscriber on the Bus keeps the newest 500 messages with a sequence
number. A caller reads the ones after its last number, filtered by its
audience (``ui`` for an owner, ``package`` for a package token) with the
same ``visible_to`` rule the Bus uses, and a package sees
``checklist.changed`` only for its own steps.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

KEEP = 500


class Feed:
    def __init__(self, bus: Any) -> None:
        self.bus = bus
        self._items: deque[tuple[int, dict]] = deque(maxlen=KEEP)
        self._seq = 0
        self._ended = 0                        # bumped by end_streams: open streams end (the Core stops)
        self._cond = threading.Condition()
        bus.subscribe("*", self._on_message, audience="core")

    def _on_message(self, message: dict) -> None:
        with self._cond:
            self._seq += 1
            self._items.append((self._seq, message))
            self._cond.notify_all()

    @property
    def last(self) -> int:
        return self._seq

    def _allowed(self, message: dict, audience: str, package_id: str | None) -> bool:
        if not self.bus.contract.visible(message["type"], audience):
            return False
        if audience == "package" and message["type"] == "checklist.changed":
            return message["payload"].get("package") == package_id
        return True

    def since(self, after: int, audience: str, package_id: str | None = None) -> list[tuple[int, dict]]:
        with self._cond:
            items = list(self._items)
        return [(n, m) for n, m in items if n > after and self._allowed(m, audience, package_id)]

    @property
    def ended(self) -> int:
        return self._ended

    def end_streams(self) -> None:
        """End every stream open now (the Core is stopping), so no listener waits on one. Later streams work."""
        with self._cond:
            self._ended += 1
            self._cond.notify_all()

    def wait(self, after: int, timeout: float, ended: int | None = None) -> bool:
        """Wait until something newer than *after* arrives (any audience), or the streams opened before
        :meth:`end_streams` (*ended* = :attr:`ended` when the stream began) must end. True if either."""
        with self._cond:
            return self._cond.wait_for(lambda: self._seq > after or (ended is not None and self._ended != ended),
                                       timeout)
