# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/agents/envelope.py (adapted)
"""The Bus envelope and budget (01 "Bus": ``intent, payload, requester, budget, changes, id``).

Every request between agents travels as an :class:`Envelope`. It carries who
is asking (``requester``, a person or profile, never an agent), which agent
put it on the Bus (``origin_agent``), the ``intent`` and ``payload``, and a
:class:`Budget` that bounds how much work it may do.

Two rules live here so no caller can forget them:

* ``changes`` is an explicit flag, not something the Bus guesses. Anything
  that changes data, spends money, sends messages or touches devices sets it,
  and that sends the envelope through Drawbridge's review first.
* Budgets count down as actions run (:meth:`Budget.consume`). Going over the
  action count or the time limit, or asking for egress the budget does not
  allow, raises :class:`BudgetExceeded`, which the Bus turns into an audited stop.

``Envelope.audit_view`` gives payload **keys only**, never values.

Changes from v1: ``state_changing`` is now ``changes`` (06 "Bus").
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetExceeded(RuntimeError):
    """An envelope's action, time or egress budget ran out.

    The Bus catches it and turns it into an audited ``aborted`` reply.
    """


@dataclass
class Budget:
    """Bounds the work one envelope may do.

    Args:
        max_actions: most side-effecting steps.
        max_seconds: wall-clock limit from the first :meth:`consume`.
        allow_egress: whether the request may reach outside this computer.
            Default False (fail closed).
    """

    max_actions: int = 1
    max_seconds: float = 30.0
    allow_egress: bool = False
    actions_used: int = 0
    _started_at: float | None = field(default=None, repr=False)

    def start(self) -> None:
        """Start the clock (idempotent)."""
        if self._started_at is None:
            self._started_at = time.monotonic()

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    @property
    def actions_remaining(self) -> int:
        return max(0, self.max_actions - self.actions_used)

    def consume(self, n: int = 1, *, egress: bool = False) -> None:
        """Charge *n* actions. The check runs before the action is counted,
        so an over-budget action never runs."""
        self.start()
        if egress and not self.allow_egress:
            raise BudgetExceeded("egress requested but budget.allow_egress is False")
        if self.elapsed > self.max_seconds:
            raise BudgetExceeded(
                f"time budget exhausted ({self.elapsed:.2f}s > {self.max_seconds}s)"
            )
        if self.actions_used + n > self.max_actions:
            raise BudgetExceeded(
                f"action budget exhausted ({self.actions_used + n} > {self.max_actions})"
            )
        self.actions_used += n

    def snapshot(self) -> dict:
        """A view for audit lines, with nothing private in it."""
        return {
            "max_actions": self.max_actions,
            "actions_used": self.actions_used,
            "max_seconds": self.max_seconds,
            "elapsed": round(self.elapsed, 3),
            "allow_egress": self.allow_egress,
        }


@dataclass
class Envelope:
    """One request between agents."""

    intent: str
    payload: dict = field(default_factory=dict)
    requester: str = "system"
    origin_agent: str = "system"
    budget: Budget = field(default_factory=Budget)
    changes: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_utcnow)

    def audit_view(self) -> dict:
        """For the audit log: payload keys only, no values."""
        try:
            payload_keys = sorted(str(k) for k in (self.payload or {}).keys())
        except Exception:
            payload_keys = []
        return {
            "id": self.id,
            "intent": self.intent,
            "requester": self.requester,
            "origin_agent": self.origin_agent,
            "changes": self.changes,
            "payload_keys": payload_keys,
            "budget": self.budget.snapshot(),
            "created_at": self.created_at,
        }


@dataclass
class ReviewVerdict:
    """Drawbridge's ruling on a ``changes: true`` envelope.

    ``allowed`` is what the Bus enforces (fail closed). ``verdict`` is
    ``allow`` | ``flag`` | ``deny`` for the audit log; ``flag`` is allowed
    but noted.
    """

    allowed: bool
    reason: str = ""
    verdict: str = "allow"


__all__ = ["Budget", "BudgetExceeded", "Envelope", "ReviewVerdict"]
