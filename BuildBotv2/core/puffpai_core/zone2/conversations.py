r"""The one conversation store: ``<state>\chat\<conversation>.jsonl`` (01 "Concierge": one answer path).

Chat, voice and routines share one conversation (``main``) unless a caller
names another. Every line holds the gate's locked form only: handles, never
held values (X6). The brain's own words are kept as the brain said them,
apart from that.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT = "main"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
HISTORY_TURNS = 20


def valid_id(conversation: object) -> bool:
    """The rule for a conversation's id: it names a file, so nothing else gets through."""
    return isinstance(conversation, str) and _ID.fullmatch(conversation) is not None


class ConversationStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._lock = threading.Lock()

    def _path(self, conversation: str) -> Path:
        if not _ID.fullmatch(conversation or ""):       # fullmatch: "$" alone lets a trailing newline through
            raise ValueError("A conversation id is letters, digits, - and _ only.")
        return self.folder / f"{conversation}.jsonl"

    def append(self, conversation: str, role: str, content: str, **extra) -> None:
        line = {"role": role, "content": content, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **extra}
        path = self._path(conversation)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")

    def load(self, conversation: str, limit: int = HISTORY_TURNS * 2) -> list[dict]:
        try:
            lines = self._path(conversation).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


__all__ = ["DEFAULT", "ConversationStore", "valid_id"]
