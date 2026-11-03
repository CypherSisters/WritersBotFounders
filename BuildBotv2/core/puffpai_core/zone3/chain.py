"""A hash-chained JSONL log: the Bridge ledger and the audit log (X14, 09 section 3).

Each line carries ``n`` (its number), ``prev`` (the hash of the line before)
and ``h`` (the SHA-256 of the line without ``h``). The first line of a file
has ``n == 1`` and ``prev == GENESIS``; each next line has ``n + 1`` and the
last ``h``. :func:`verify` walks a whole file by those rules (Caretaker runs
it daily), so a cut at the head or in the middle shows.

**The sealed head.** After every line written, the writer keeps
``<name>.head`` = ``{n, h, mac}``, the MAC under the machine key
(:mod:`..anchor`). At start the file must verify AND its last line must be
the sealed ``n`` and ``h``. So a tail cut (lines dropped at the end), a head
cut, a deleted file or a whole chain recomputed with plain hashes is caught
while the Core was stopped. While running, before every append the writer
checks the file's last line against its own head in memory, and, when the
file's size or time moved, hashes it again against a running hash of every
byte it wrote; so an edit or a cut shows at the next line written.

On a mismatch the damaged file is moved aside (``<name>.damaged-<time>.jsonl``,
never deleted), a new file starts with a ``chain_restarted`` line that names
the damaged file's hash, and ``on_tamper`` is called (the Bridge closes and a
card says why). Past ``max_bytes`` the file rotates to ``<name>.1.jsonl``; the
new file starts with a ``chain_rotated`` line naming the old file's last
``n`` and ``h``, and at start the old file's last line is checked against it.

Honest limits: this detects, it does not prevent. A program written to target
PuffPAI can unwrap the machine key (the machine wrap unwraps for any program running as
the owner) and forge the seal; the vault copy of the head (``seal``, Archivist
at lock and stop, lane L3) is what stops that. A hard crash between writing a
line and writing its seal reads as damaged at the next start (fail closed).
No machine key: nothing can be sealed or checked, and ``on_tamper`` hears
``no_machine_key`` once. Lines hold names and codes, never values.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import anchor as _anchor

GENESIS = "0" * 64
MAX_BYTES = 8 * 1024 * 1024


def _canon(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def line_hash(line: dict) -> str:
    body = {k: v for k, v in line.items() if k != "h"}
    return hashlib.sha256(_canon(body).encode("ascii")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def verify(path: Path | str) -> tuple[bool, int]:
    """``(ok, bad_line_number)``. A missing or empty file is ok (nothing written yet).
    The first line must be ``n == 1`` with ``prev == GENESIS``; ``bad_line_number`` is 1-based."""
    p = Path(path)
    if not p.exists():
        return True, 0
    prev, n = GENESIS, 0
    with open(p, "r", encoding="ascii", errors="replace") as fh:
        for i, raw in enumerate(fh, 1):
            try:
                line = json.loads(raw)
            except ValueError:
                return False, i
            if not isinstance(line, dict) or line.get("h") != line_hash(line):
                return False, i
            if line.get("prev") != prev or line.get("n") != n + 1 or isinstance(line.get("n"), bool):
                return False, i
            prev, n = line["h"], line["n"]
    return True, 0


def _last_line(path: Path) -> dict | None:
    """The last JSON line of *path*, or None (empty or missing). Raises ValueError on junk."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size == 0:
        return None
    with open(path, "rb") as fh:
        step = 4096
        data = b""
        pos = size
        while pos > 0:
            take = min(step, pos)
            pos -= take
            fh.seek(pos)
            data = fh.read(take) + data
            body = data.rstrip(b"\n")
            if b"\n" in body or pos == 0:
                last = body.rsplit(b"\n", 1)[-1]
                return json.loads(last.decode("ascii"))
            step *= 2
    return None


def _first_line(path: Path) -> dict | None:
    try:
        with open(path, "r", encoding="ascii", errors="replace") as fh:
            raw = fh.readline()
    except OSError:
        return None
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


class ChainLog:
    """One chained log file. Thread-safe; one writer per file (the Core).

    ``anchor``: the machine-key MAC for the sealed head (default: the bot
    folder's, for a file at ``<state>/logs/<name>``). ``seal(head, n)``: the
    vault copy (lane L3), called after each line.
    """

    def __init__(self, path: Path | str, *, on_tamper: Callable[[dict], None] | None = None,
                 seal: Callable[[str, int], None] | None = None, max_bytes: int = MAX_BYTES,
                 anchor: _anchor.Anchor | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.head_path = self.path.with_name(self.path.name + ".head")
        # <state>/logs/<name> -> the bot folder's anchor; a log anywhere else keeps its key beside it
        base = self.path.parent.parent if self.path.parent.name == "logs" else self.path.parent
        self.anchor = anchor or _anchor.for_state(base)
        self.on_tamper = on_tamper
        self.seal = seal
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self.write_lock = self._lock           # D151: held across a line and its seal; the unlock check reads under it
        self.tampered: list[dict] = []
        self._digest = hashlib.sha256()
        self._stat: tuple | None = None
        self._head, self._n = GENESIS, 0
        problem = self._start_check()
        if problem is None:
            self._rehash()
        elif problem.get("why") == "no_machine_key":
            self._rehash()
            self.tampered.append(problem)
            self._tell(problem)
        else:
            self._tell(self._restart(problem))

    # -- the start check --------------------------------------------------------
    def _read_seal(self) -> dict | None:
        try:
            return json.loads(self.head_path.read_text(encoding="ascii"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return {"bad": True}

    def _seal_text(self, n: int, h: str) -> str:
        return f"{self.path.name}:{n}:{h}"

    def _start_check(self) -> dict | None:
        """None when the file is whole and matches its sealed head; else the reason."""
        ok, bad = verify(self.path)
        if not ok:
            return {"why": "verify_failed", "line": bad}
        try:
            last = _last_line(self.path)
        except ValueError:
            return {"why": "verify_failed", "line": 0}
        if last is not None:
            self._head, self._n = last["h"], int(last["n"])
        if not self.anchor.available:
            return {"why": "no_machine_key"}
        seal = self._read_seal()
        if seal is None:
            if last is None:
                return None                    # a new log: nothing written, nothing sealed
            return {"why": "no_sealed_head"}
        if seal.get("bad") or not isinstance(seal.get("n"), int) or not isinstance(seal.get("h"), str) \
                or not self.anchor.check("chain", self._seal_text(seal["n"], seal["h"]), seal.get("mac")):
            return {"why": "seal_damaged"}
        if last is None:
            return {"why": "file_missing"} if seal["n"] > 0 else None
        if (seal["n"], seal["h"]) != (self._n, self._head):
            return {"why": "head_mismatch", "sealed_n": seal["n"], "file_n": self._n}
        first = _first_line(self.path) or {}
        if first.get("event") == "chain_rotated":
            old = self.path.with_name(self.path.stem + ".1" + self.path.suffix)
            try:
                old_last = _last_line(old)
            except ValueError:
                old_last = None
            if old_last is None or (old_last.get("n"), old_last.get("h")) != (first.get("from_n"), first.get("from_h")) \
                    or not verify(old)[0]:
                return {"why": "rotated_cut"}
        return None

    def _write_seal(self) -> None:
        if not self.anchor.available:
            return
        text = _canon({"n": self._n, "h": self._head,
                       "mac": self.anchor.mac("chain", self._seal_text(self._n, self._head))})
        tmp = self.head_path.with_name(self.head_path.name + ".tmp")
        tmp.write_text(text, encoding="ascii")
        for _ in range(5):
            try:
                os.replace(tmp, self.head_path)
                return
            except PermissionError:        # Windows: a reader has it open
                time.sleep(0.02)

    def _file_stat(self) -> tuple | None:
        try:
            st = self.path.stat()
            return (st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            return None

    def _rehash(self) -> None:
        """Running hash and stat from the file as it is now (after a check passed)."""
        self._digest = hashlib.sha256(self.path.read_bytes() if self.path.exists() else b"")
        self._stat = self._file_stat()

    def _intact(self) -> bool:
        try:
            last = _last_line(self.path)
        except (ValueError, OSError):
            return False
        if (last["h"] if last else GENESIS) != self._head or (int(last["n"]) if last else 0) != self._n:
            return False                       # a cut or an edit of the last line, even with its time put back
        if last is not None and line_hash(last) != last["h"]:
            return False
        if self._file_stat() == self._stat:
            return True
        try:
            now_bytes = self.path.read_bytes() if self.path.exists() else b""
        except OSError:
            return False
        if hashlib.sha256(now_bytes).digest() != self._digest.digest():
            return False
        self._stat = self._file_stat()          # touched, not changed
        return True

    # -- reading ------------------------------------------------------------
    def head(self) -> tuple[str, int]:
        with self._lock:
            return self._head, self._n

    def read(self, limit: int = 200) -> list[dict]:
        """The newest *limit* lines, newest first. Never raises."""
        try:
            with open(self.path, "r", encoding="ascii", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        out = []
        for raw in reversed(lines[-max(1, min(int(limit), 2000)):]):
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out

    # -- writing ------------------------------------------------------------
    def _write_line(self, line: dict) -> None:
        line["n"] = self._n + 1
        line["prev"] = self._head
        line["h"] = line_hash(line)
        text = _canon(line) + "\n"
        with open(self.path, "a", encoding="ascii", newline="\n") as fh:
            fh.write(text)
            fh.flush()
        self._digest.update(text.encode("ascii"))
        self._stat = self._file_stat()
        self._head, self._n = line["h"], line["n"]
        self._write_seal()
        if self.seal is not None:
            try:
                self.seal(self._head, self._n)   # every line, restarts and rotations too (D54)
            except Exception:  # noqa: BLE001 - sealing is evidence, never a reason to drop a line
                pass

    def append(self, record: dict[str, Any]) -> str:
        """Append one record (names and codes only). Returns the line hash."""
        note = None
        with self._lock:
            if not self._intact():
                note = self._restart({"why": "head_mismatch"})
            line = {k: v for k, v in record.items() if k not in ("n", "prev", "h")}
            line.setdefault("t", now())
            text_len = len(_canon(line)) + 200
            self._rotate_if_needed(text_len)
            self._write_line(line)
            head, n = self._head, self._n
        if note is not None:
            self._tell(note)
        return head

    def _rotate_if_needed(self, adding: int) -> bool:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return False
        if size + adding <= self.max_bytes:
            return False
        old = self.path.with_name(self.path.stem + ".1" + self.path.suffix)
        for _ in range(5):
            try:
                os.replace(self.path, old)
                break
            except PermissionError:        # Windows: a reader has it open
                time.sleep(0.05)
        else:
            return False
        from_h, from_n = self._head, self._n
        self._head, self._n = GENESIS, 0
        self._digest = hashlib.sha256()
        self._stat = None
        self._write_line({"event": "chain_rotated", "from_n": from_n, "from_h": from_h, "t": now()})
        return True

    def _tell(self, note: dict) -> None:
        if self.on_tamper is not None:
            try:
                self.on_tamper(note)
            except Exception:  # noqa: BLE001
                pass

    def _restart(self, info: dict) -> dict:
        """Move a damaged file aside, never delete it, and start a new chain."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        aside = self.path.with_name(f"{self.path.stem}.damaged-{stamp}{self.path.suffix}")
        file_sha = ""
        if self.path.exists():
            file_sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
            os.replace(self.path, aside)
        self._head, self._n = GENESIS, 0
        self._digest = hashlib.sha256()
        self._stat = None
        self._write_line({"event": "chain_restarted", "why": str(info.get("why", "unknown")),
                          "damaged_sha": file_sha or GENESIS, "t": now()})
        note = dict(info, aside=aside.name if file_sha else "")
        self.tampered.append(note)
        return note


__all__ = ["ChainLog", "GENESIS", "line_hash", "now", "verify"]
