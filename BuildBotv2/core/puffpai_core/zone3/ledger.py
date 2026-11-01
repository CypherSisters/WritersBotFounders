# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/egress_ledger.py (adapted)
"""The outside log: one line for every request that tried to leave this computer.

``<state>/logs/outside.jsonl``, written only by the Core (X14, Z3 F7), as a
hash chain (:mod:`.chain`). Every field is checked against a shape or a
closed list in :func:`build_record`, so an odd caller produces a smaller line,
never a leak: no query string, no path, no header, no body, no key. Refusals
are written too. What changed from v1: a hash chain instead of plain JSONL
(no silent prune: the file rotates once at 8 MB), the ``subject`` and ``phase``
of the Bridge decision, and the reason codes of ``egress.decision``.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Callable

from ..zone1 import secret_detector
from . import hosts
from .chain import ChainLog

LOG_NAME = "outside.jsonl"
OUTCOMES = ("done", "refused", "failed")
VIAS = ("proxy", "core", "direct", "docker")
PHASES = ("run", "install", "build")
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "CONNECT"})

#: Closed on purpose: a free-text reason could hold a host or a value someone typed.
REASON_CODES = frozenset({
    "",
    # egress.decision
    "bridge_closed", "switched_off", "not_on_list", "blocked_by_provider", "labelled", "needs_yes", "granted",
    # the proxy and the connection itself (v1 egress_proxy / egress_ledger)
    "inside_address", "network_filter", "cannot_look_up", "no_answer", "not_encrypted", "not_allowed",
    "bad_answer", "too_big", "bad_credential", "lan_only",
    # v2
    "around_the_bridge", "drawbridge_down", "policy_damaged", "probe",
})

_SUBJECT_RE = re.compile(r"^(pkg:[a-z][a-z0-9_-]{1,39}|agent:[a-z]+|core:[a-z0-9_-]+|toolsmith:(cloud|search|read)"
                         r"|provider:[a-z][a-z0-9_-]{1,39}|unknown)$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")


def _count(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _keyish(text: str) -> bool:
    try:
        return any(h.kind != "generated_key" for h in secret_detector.find(text))
    except Exception:  # noqa: BLE001 - fail closed: treated as a key
        return True


def safe_host(host: Any) -> str:
    h = hosts.clean(host)
    if not h:
        return ""
    if hosts.is_ip_literal(h):
        try:
            import ipaddress
            return str(ipaddress.ip_address(h.split("%", 1)[0]))
        except ValueError:
            return ""
    if not hosts.HOST_RE.match(h) or _keyish(h) or _keyish(str(host)):     # as written: case shows a key ID
        return ""
    return h


def build_record(subject: str, *, host: Any, outcome: str, code: str = "", via: str = "proxy",
                 phase: str = "run", port: Any = None, method: str = "CONNECT", bytes_in: Any = 0,
                 bytes_out: Any = 0, req_id: str | None = None, rule: str | None = None) -> dict | None:
    """One clean line, or None when the inputs can't make one."""
    sid = str(subject or "")
    if not _SUBJECT_RE.match(sid) or outcome not in OUTCOMES:
        return None
    reason = str(code or "")
    if reason not in REASON_CODES:
        reason = "not_allowed" if outcome == "refused" else "no_answer"
    verb = str(method or "CONNECT").upper()
    p = _count(port)
    rec = {
        "subject": sid, "host": safe_host(host), "port": p if p is not None and p < 65536 else None,
        "method": verb if verb in METHODS else "CONNECT", "via": via if via in VIAS else "proxy",
        "phase": phase if phase in PHASES else "run", "outcome": outcome, "code": reason,
        "bytes_in": _count(bytes_in), "bytes_out": _count(bytes_out),
    }
    if req_id and _ID_RE.match(str(req_id)):
        rec["req_id"] = str(req_id)
    if rule and _ID_RE.match(str(rule)) and not _keyish(str(rule)):
        rec["rule"] = str(rule)
    return rec


#: The refused hosts kept in memory per subject (D70), so a try's reply can name them: at most this many
#: hosts per subject and this many subjects; the oldest go first. The file keeps every line.
REFUSED_HOSTS_MAX = 50
REFUSED_SUBJECTS_MAX = 200


class Ledger:
    """The Bridge's outside log on a hash chain."""

    def __init__(self, path: Path | str, *, on_tamper: Callable[[dict], None] | None = None,
                 seal: Callable[[str, int], None] | None = None, anchor: Any = None) -> None:
        self.chain = ChainLog(path, on_tamper=on_tamper, seal=seal, anchor=anchor)
        self._refused: dict[str, dict[str, tuple[int, str]]] = {}   # subject -> host -> (seq, code)
        self._refused_seq = 0
        self._refused_lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.chain.path

    def record(self, subject: str, **fields: Any) -> str | None:
        """Build and append one line; returns its id (the line hash), or None. Never raises."""
        try:
            rec = build_record(subject, **fields)
            if rec is None:
                return None
            if rec["outcome"] == "refused" and rec["host"]:
                self._note_refused(rec["subject"], rec["host"], rec["code"])
            return self.chain.append(rec)
        except Exception:  # noqa: BLE001 - the decision already stands; losing a line loses evidence only
            return None

    def _note_refused(self, subject: str, host: str, code: str) -> None:
        with self._refused_lock:
            self._refused_seq += 1
            hosts_of = self._refused.pop(subject, None) or {}
            hosts_of.pop(host, None)
            hosts_of[host] = (self._refused_seq, code)
            while len(hosts_of) > REFUSED_HOSTS_MAX:
                hosts_of.pop(next(iter(hosts_of)))
            self._refused[subject] = hosts_of            # the newest subject last
            while len(self._refused) > REFUSED_SUBJECTS_MAX:
                self._refused.pop(next(iter(self._refused)))

    def refused_since(self, subject: str, after: int = 0) -> tuple[list[dict], int]:
        """``([{host, code}], mark)``: the hosts the Bridge refused for *subject* after *after* (a mark this
        call gave before; 0 = since the Core started), oldest first, and the mark to pass next time. Host
        names and closed codes only, as in the file (D70)."""
        with self._refused_lock:
            mark = self._refused_seq
            got = sorted((seq, host, code) for host, (seq, code) in (self._refused.get(str(subject)) or {}).items()
                         if seq > after)
        return [{"host": host, "code": code} for _seq, host, code in got], mark

    def read(self, limit: int = 200) -> list[dict]:
        out = []
        for line in self.chain.read(limit):
            if line.get("event") == "chain_restarted":
                out.append({"t": line.get("t"), "event": "chain_restarted", "why": line.get("why")})
                continue
            if line.get("event") == "chain_rotated":
                out.append({"t": line.get("t"), "event": "chain_rotated"})
                continue
            rec = build_record(str(line.get("subject") or ""), host=line.get("host"),
                               outcome=str(line.get("outcome") or ""), code=str(line.get("code") or ""),
                               via=str(line.get("via") or ""), phase=str(line.get("phase") or ""),
                               port=line.get("port"), method=str(line.get("method") or ""),
                               bytes_in=line.get("bytes_in"), bytes_out=line.get("bytes_out"),
                               req_id=line.get("req_id"), rule=line.get("rule"))
            if rec is not None:
                rec["t"] = line.get("t")
                out.append(rec)
        return out


__all__ = ["LOG_NAME", "Ledger", "OUTCOMES", "REASON_CODES", "build_record", "safe_host"]
