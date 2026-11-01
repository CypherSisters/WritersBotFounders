"""The Bus (lane L0): envelopes between agents, plus typed events and checks.

Interface: ``Bus(contract=None, audit=None)`` with ``register``, ``send``
(envelopes, Drawbridge review for ``changes: true``), ``Bus.message(...)``
(build an event or check), ``publish(event) -> bool``, ``ask(check) -> reply``,
``subscribe(pattern, handler, audience)``, ``answer(type, handler, by)``.
``Source(kind, id)`` names who sends. ``Envelope``, ``Budget``,
``ReviewVerdict`` come from ``bus.envelope``. Message names and fields:
``contracts/events.json``.
"""

from .bus import BUS_SOURCE, Bus, Contract, DEFAULT_REVIEWER, Rejected, Source
from .envelope import Budget, BudgetExceeded, Envelope, ReviewVerdict

__all__ = ["BUS_SOURCE", "Budget", "BudgetExceeded", "Bus", "Contract", "DEFAULT_REVIEWER",
           "Envelope", "Rejected", "ReviewVerdict", "Source"]
