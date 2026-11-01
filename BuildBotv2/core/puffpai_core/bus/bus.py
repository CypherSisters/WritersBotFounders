# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/agents/bus.py (adapted)
"""The Bus: one per bot, built once (01 "Bus", ZONES_TOGETHER section 2).

Two layers on one object:

1. **Envelope routing** (harvested from v1 ``AgentBus``): ``register(agent)``,
   ``send(envelope, to)``. A ``changes: true`` envelope goes to the reviewer
   (Drawbridge) first. No reviewer, a crashing review or an odd answer is a
   deny (fail closed). Every send is audited with payload keys only.

2. **Typed messages** from ``contracts/events.json``:
   - ``publish(event)``: fire and forget.
   - ``ask(check) -> reply``: waits until ``deadline_ms``. No answer by the
     deadline is the safe answer (deny); so are no answerer, a crash and an
     answer outside the check's verdict list.
   Every message is refused (and ``bus.rejected`` published) when:
   its type is unknown or its envelope or payload breaks the schema; a field
   named in ``forbidden_field_names`` appears anywhere in the payload; any
   string or key holds a key shape (the floor key detector); or its source may
   not publish that type. Only the Core publishes ``vault.*``,
   ``egress.decision``, ``consent.*`` and ``provider.*``.

Subscribers are wired by the Core with an audience (core, agent, provider,
package, ui); a message reaches only the audiences in its ``visible_to``.
Packages and providers never subscribe in process: the Core serves them
``/v1/events`` filtered the same way.

Key check (D50, one shared rule with the gate before the model): every string
and dict key in a message goes through ``zone1.secret_detector`` (every kind,
``generated_key`` too) and ``zone1.token_rules`` (more prefixes, values next to
a key word, random-looking tokens, keys split across lines). A hit drops the
message (``bus.rejected``, why ``key_shape``). The only values let through
whole are the shapes the Core itself mints, each in its own field: a message
id ``ev-`` plus 16 hex (``Bus.message`` makes it; any other id is refused), a
session of 16 hex, and a hex hash under a field named for one (``*_sha``,
``*_hash``, ``sha``, ``hash``), and, in any field, a UUID written as 32 hex
with no dashes when its version (4 or 7) and variant bits are right. Git commit
ids and dashed UUIDs are harmless shapes in ``token_rules`` itself.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from jsonschema import Draft202012Validator

from ..paths import contracts_dir
from ..zone1 import secret_detector, token_rules
from .envelope import BudgetExceeded, Envelope, ReviewVerdict

logger = logging.getLogger(__name__)

DEFAULT_REVIEWER = "drawbridge"
EVENT_ID_PREFIX = "ev-"
EVENT_ID = re.compile(r"ev-[0-9a-f]{16}")
#: Core-minted shapes allowed whole, by field name (the last part of the path).
_SAFE_FIELDS: tuple[tuple[Callable[[str], bool], re.Pattern[str]], ...] = (
    (lambda f: f == "id", EVENT_ID),
    (lambda f: f == "session", re.compile(r"[0-9a-f]{16}")),
    (lambda f: f in ("sha", "hash") or f.endswith(("_sha", "_hash")), re.compile(r"[0-9a-f]{16,128}")),
)
BUS_SOURCE = {"kind": "core", "id": "core:bus"}

Handler = Callable[[dict], Any]
AuditFn = Callable[..., None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class Source:
    """Who puts a message on the Bus: ``kind`` is core | agent | provider | package."""

    kind: str
    id: str

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id}


class Rejected(Exception):
    def __init__(self, why: str, field: str = "") -> None:
        super().__init__(why)
        self.why = why
        self.field = field


class Contract:
    """``contracts/events.json``, loaded and compiled."""

    def __init__(self, data: dict | None = None) -> None:
        if data is None:
            data = json.loads((contracts_dir() / "events.json").read_text(encoding="utf-8"))
        self.data = data
        self.core_only: list[str] = list(data["core_only_namespaces"])
        self.forbidden: frozenset[str] = frozenset(data["forbidden_field_names"])
        self.audiences: frozenset[str] = frozenset(data["audiences"])
        self.messages: dict[str, dict] = data["messages"]
        defs = data["$defs"]
        env = dict(data["envelope"]["event"])
        self._event = Draft202012Validator({**env, "$defs": defs})
        check = json.loads(json.dumps(env))
        check["required"] = env["required"] + data["envelope"]["check"]["required"]
        check["properties"].update(data["envelope"]["check"]["properties"])
        self._check = Draft202012Validator({**check, "$defs": defs})
        self._payload = {
            name: Draft202012Validator({**spec["payload"], "$defs": defs})
            for name, spec in self.messages.items()
        }
        self._code = Draft202012Validator({**defs["code"], "$defs": defs})

    def is_core_only(self, type_: str) -> bool:
        return any(fnmatch.fnmatchcase(type_, ns) for ns in self.core_only)

    def may_publish(self, type_: str, source_kind: str) -> bool:
        if self.is_core_only(type_):
            return source_kind == "core"
        return source_kind in self.messages[type_]["publishers"]

    def visible(self, type_: str, audience: str) -> bool:
        return audience in self.messages[type_]["visible_to"]

    def check(self, message: Any, form: str) -> None:
        """Raise :class:`Rejected` if the message may not travel."""
        if not isinstance(message, dict):
            raise Rejected("schema")
        type_ = message.get("type")
        spec = self.messages.get(type_) if isinstance(type_, str) else None
        if spec is None:
            raise Rejected("schema", "type")
        if spec["form"] != form:
            raise Rejected("schema", "type")
        validator = self._check if form == "check" else self._event
        for err in validator.iter_errors(message):
            raise Rejected("schema", _path(err.absolute_path))
        if not EVENT_ID.fullmatch(message["id"]):
            raise Rejected("schema", "id")             # ids come from Bus.message, one recognised shape
        _scan_keys(message, "")
        if not self.may_publish(type_, message["source"]["kind"]):
            raise Rejected("publisher", "source")
        _scan_forbidden(message["payload"], "payload", self.forbidden)
        for err in self._payload[type_].iter_errors(message["payload"]):
            raise Rejected("schema", _path(["payload", *err.absolute_path]))

    def good_code(self, code: Any) -> bool:
        return isinstance(code, str) and self._code.is_valid(code)


def _path(parts: Any) -> str:
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _field(where: str) -> str:
    return re.sub(r"\[\d+\]$", "", where).rsplit(".", 1)[-1]


def _key_hit(text: str, where: str = "") -> bool:
    """The one floor key rule (D50): ``secret_detector`` (every kind) plus ``token_rules``."""
    field = _field(where)
    if any(applies(field) and shape.fullmatch(text) for applies, shape in _SAFE_FIELDS):
        return False
    if _bare_uuid(text):
        return False
    return bool(secret_detector.find(text)) or bool(token_rules.find(text))


_HEX32 = re.compile(r"[0-9a-f]{32}")
BARE_UUID_VERSIONS = (4, 7)


def _bare_uuid(text: str) -> bool:
    """A UUID written as 32 hex with no dashes (``uuid4().hex``): allowed only when its variant bits are RFC
    9562's and its version is 4 or 7 (the random and time-ordered ones). A random 32-hex key passes that by
    chance about 1 time in 32, so only those versions, not all eight."""
    if not _HEX32.fullmatch(text):
        return False
    u = uuid.UUID(hex=text)
    return u.variant == uuid.RFC_4122 and u.version in BARE_UUID_VERSIONS


def _scan_keys(node: Any, where: str) -> None:
    if isinstance(node, str):
        if _key_hit(node, where):
            raise Rejected("key_shape", where)
    elif isinstance(node, dict):
        for k, v in node.items():
            here = f"{where}.{k}" if where else str(k)
            if _key_hit(str(k)):
                raise Rejected("key_shape", where or "payload")
            _scan_keys(v, here)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _scan_keys(v, f"{where}[{i}]")


def _scan_forbidden(node: Any, where: str, forbidden: frozenset[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{where}.{k}"
            if str(k).lower() in forbidden:
                raise Rejected("schema", here)
            _scan_forbidden(v, here, forbidden)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan_forbidden(v, f"{where}[{i}]", forbidden)


def _memory_audit(store: deque) -> AuditFn:
    def audit(action: str, **fields: Any) -> None:
        store.append({"at": _now(), "action": action, **fields})
    return audit


class Bus:
    """The one Bus. See the module docstring."""

    def __init__(self, contract: Contract | None = None, audit: AuditFn | None = None,
                 reviewer_name: str = DEFAULT_REVIEWER) -> None:
        self.contract = contract or Contract()
        self.audit_lines: deque = deque(maxlen=1000)
        self._audit = audit or _memory_audit(self.audit_lines)
        self._agents: dict[str, Any] = {}
        self._reviewer_name = reviewer_name
        self._subs: list[tuple[str, Handler, str]] = []
        self._answerers: dict[str, tuple[Handler, Source]] = {}
        self._lock = threading.RLock()

    # -- envelope routing (v1 AgentBus) -------------------------------------
    def register(self, agent: Any) -> Any:
        """Add an agent and wire the Bus back into it."""
        name = getattr(agent, "name", None)
        if not name:
            raise ValueError("agent must have a non-empty .name")
        if name in self._agents:
            raise ValueError(f"agent name already registered: {name!r}")
        self._agents[name] = agent
        binder = getattr(agent, "bind_bus", None)
        if callable(binder):
            binder(self)
        return agent

    def set_reviewer(self, name: str) -> None:
        self._reviewer_name = name

    def get(self, name: str) -> Any:
        return self._agents.get(name)

    def agents(self) -> dict[str, Any]:
        return dict(self._agents)

    def send(self, envelope: Envelope, to: str) -> dict:
        """Route *envelope* to agent *to*: review, audit, budget."""
        agent = self._agents.get(to)
        if agent is None:
            self._audit("bus_send", result="error", details=f"unknown agent {to!r}", extra=envelope.audit_view())
            return {"ok": False, "error": f"unknown agent: {to}"}

        if envelope.changes and to != self._reviewer_name:
            verdict = self._review(envelope, to)
            if not verdict.allowed:
                self._audit("bus_veto", result="denied",
                            details=f"{envelope.origin_agent}->{to}:{envelope.intent} [{verdict.verdict}] {verdict.reason}",
                            extra=envelope.audit_view())
                return {"ok": False, "denied": True, "by": self._reviewer_name,
                        "verdict": verdict.verdict, "reason": verdict.reason}

        self._audit("bus_send", result="success", details=f"{envelope.origin_agent}->{to}:{envelope.intent}",
                    extra=envelope.audit_view())
        try:
            reply = agent.handle(envelope)
        except BudgetExceeded as exc:
            self._audit("bus_budget_abort", result="aborted",
                        details=f"{envelope.origin_agent}->{to}:{envelope.intent}: {exc}", extra=envelope.audit_view())
            return {"ok": False, "aborted": "budget", "reason": str(exc)}
        except Exception as exc:  # a crashing handler must not take down the Bus
            logger.exception("agent %s failed handling %s", to, envelope.intent)
            self._audit("bus_error", result="error", details=f"{envelope.origin_agent}->{to}:{envelope.intent}: {exc}",
                        extra=envelope.audit_view())
            return {"ok": False, "error": str(exc)}
        if not isinstance(reply, dict):
            return {"ok": True, "result": reply}
        return reply

    def _review(self, envelope: Envelope, to: str) -> ReviewVerdict:
        """Fail closed: a missing reviewer or a crashing review is a deny."""
        reviewer = self._agents.get(self._reviewer_name)
        review_fn = getattr(reviewer, "review", None) if reviewer is not None else None
        if not callable(review_fn):
            return ReviewVerdict(allowed=False, verdict="deny",
                                 reason=f"no policy reviewer {self._reviewer_name!r} registered")
        try:
            verdict = review_fn(envelope, target=to)
        except Exception as exc:
            logger.exception("policy reviewer crashed")
            return ReviewVerdict(allowed=False, verdict="deny", reason=f"review error: {exc}")
        if isinstance(verdict, ReviewVerdict):
            return verdict
        if isinstance(verdict, dict):
            allowed = verdict.get("allowed") is True
            return ReviewVerdict(allowed=allowed, verdict=verdict.get("verdict", "allow" if allowed else "deny"),
                                 reason=str(verdict.get("reason", "")))
        return ReviewVerdict(allowed=False, verdict="deny", reason="reviewer returned unrecognized verdict")

    def health(self) -> dict:
        reviewer_ok = callable(getattr(self._agents.get(self._reviewer_name), "review", None))
        return {"ok": bool(self._agents) and reviewer_ok, "agent_count": len(self._agents),
                "reviewer": self._reviewer_name, "reviewer_wired": reviewer_ok,
                "agents": sorted(self._agents.keys())}

    # -- typed messages -----------------------------------------------------
    @staticmethod
    def message(type_: str, payload: dict, source: Source | dict, *, deadline_ms: int | None = None,
                session: str | None = None, turn: str | None = None, corr: str | None = None) -> dict:
        """Build an event (or a check, with ``deadline_ms``) with a fresh id and time."""
        msg: dict[str, Any] = {
            "type": type_, "v": 1, "id": EVENT_ID_PREFIX + uuid.uuid4().hex[:16], "at": _now(),
            "source": source.as_dict() if isinstance(source, Source) else dict(source),
            "payload": payload,
        }
        for key, value in (("session", session), ("turn", turn), ("corr", corr)):
            if value is not None:
                msg[key] = value
        if deadline_ms is not None:
            msg["deadline_ms"] = deadline_ms
        return msg

    def subscribe(self, pattern: str, handler: Handler, audience: str = "core") -> Callable[[], None]:
        """Call *handler* for each delivered message whose type matches *pattern*
        (``*`` wildcards) and whose ``visible_to`` holds *audience*.
        Returns a function that unsubscribes."""
        if audience not in self.contract.audiences:
            raise ValueError(f"unknown audience {audience!r}")
        entry = (pattern, handler, audience)
        with self._lock:
            self._subs.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._subs:
                    self._subs.remove(entry)
        return unsubscribe

    def answer(self, type_: str, handler: Handler, by: Source) -> None:
        """Make *by* the one answerer of check *type_*. Only the Core or an agent answers."""
        spec = self.contract.messages.get(type_)
        if spec is None or spec["form"] != "check":
            raise ValueError(f"{type_!r} is not a check")
        if by.kind not in ("core", "agent"):
            raise ValueError("only the Core or one of its agents answers a check")
        with self._lock:
            if type_ in self._answerers:
                raise ValueError(f"{type_!r} already has an answerer")
            self._answerers[type_] = (handler, by)

    def publish(self, message: dict) -> bool:
        """Deliver an event. False (and ``bus.rejected``) if it may not travel."""
        try:
            self.contract.check(message, "event")
        except Rejected as r:
            self._reject(message, r)
            return False
        self._deliver(message)
        return True

    def ask(self, message: dict) -> dict:
        """Send a check and wait for its answer, at most ``deadline_ms``."""
        type_ = message.get("type") if isinstance(message, dict) else None
        spec = self.contract.messages.get(type_) if isinstance(type_, str) else None
        safe = spec.get("safe_verdict", "deny") if spec else "deny"
        msg_id = message.get("id") if isinstance(message, dict) and isinstance(message.get("id"), str) else "unknown"
        try:
            self.contract.check(message, "check")
        except Rejected as r:
            self._reject(message, r)
            return self._safe(msg_id, safe, "rejected")
        self._deliver(message)

        with self._lock:
            answerer = self._answerers.get(type_)
        if answerer is None:
            return self._safe(msg_id, safe, "no_answerer")
        handler, by = answerer
        box: dict[str, Any] = {}
        done = threading.Event()

        def run() -> None:
            try:
                box["answer"] = handler(message)
            except Exception as exc:  # the answerer's crash is a "no"
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=run, name=f"bus-check-{type_}", daemon=True).start()
        if not done.wait(message["deadline_ms"] / 1000.0):
            return self._safe(msg_id, safe, "no_answer")
        if "error" in box:
            logger.warning("check %s: answerer %s crashed: %s", type_, by.id, box["error"])
            return self._safe(msg_id, safe, "answer_error")
        answer = box.get("answer")
        if not isinstance(answer, dict) or answer.get("verdict") not in spec["verdicts"] \
                or not self.contract.good_code(answer.get("reason_code")):
            return self._safe(msg_id, safe, "bad_answer")
        reply = {"id": msg_id, "verdict": answer["verdict"], "reason_code": answer["reason_code"], "by": by.id}
        self._audit("bus_check", result=reply["verdict"], details=f"{type_} {reply['reason_code']}", extra={"id": msg_id})
        return reply

    def _safe(self, msg_id: str, verdict: str, code: str) -> dict:
        self._audit("bus_check", result=verdict, details=f"safe answer: {code}", extra={"id": msg_id})
        return {"id": msg_id, "verdict": verdict, "reason_code": code, "by": BUS_SOURCE["id"]}

    def _deliver(self, message: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        type_ = message["type"]
        for pattern, handler, audience in subs:
            if fnmatch.fnmatchcase(type_, pattern) and self.contract.visible(type_, audience):
                try:
                    handler(message)
                except Exception:
                    logger.exception("subscriber for %s failed on %s", pattern, type_)

    def _reject(self, message: Any, r: Rejected) -> None:
        """Publish ``bus.rejected`` with names only, never what was sent."""
        src = message.get("source") if isinstance(message, dict) else None
        source = {"kind": "package", "id": "unknown"}
        if isinstance(src, dict):
            kind, sid = src.get("kind"), src.get("id")
            if kind in ("core", "agent", "provider", "package"):
                source["kind"] = kind
            if isinstance(sid, str) and len(sid) <= 128 and not _key_hit(sid) \
                    and all(c.isalnum() or c in "_.:@/-" for c in sid) and sid:
                source["id"] = sid
        type_ = message.get("type") if isinstance(message, dict) else None
        if not (isinstance(type_, str) and 0 < len(type_) <= 64 and type_[0].isalpha()
                and all(c.islower() or c in "_." for c in type_)):
            type_ = "unknown"
        field = r.field if (len(r.field) <= 128 and not _key_hit(r.field)
                            and all(c.isalnum() or c in "_.[]" for c in r.field)) else "unknown"
        payload = {"source": source, "type": type_, "field": field, "why": r.why}
        rejected = self.message("bus.rejected", payload, BUS_SOURCE)
        self._audit("bus_rejected", result="dropped", details=f"{type_} {r.why} at {field}", extra={"source": source})
        try:
            self.contract.check(rejected, "event")
        except Rejected:  # never loop; the audit line above is the record
            logger.error("bus.rejected itself failed its schema")
            return
        self._deliver(rejected)


__all__ = ["BUS_SOURCE", "Bus", "Contract", "DEFAULT_REVIEWER", "Rejected", "Source"]
