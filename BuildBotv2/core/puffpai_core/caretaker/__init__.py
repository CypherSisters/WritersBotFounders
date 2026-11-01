"""Caretaker (lane L1): health checks, restarts, ``broken`` marks, the self-test and safe mode.

Interface: ``Caretaker(registry, steward, bus)`` with ``probe(id)``,
``check(id)``, ``started(id)``, ``tick()``, ``start()`` / ``stop()`` (the
background loop), ``refresh_tools(id)``, ``mark_broken(id, reason)``,
``selftest(checks=None)``, ``safe_mode(reason)`` (the one runtime safe-mode call),
``in_safe_mode`` and ``safe_reasons``, ``safe_steps`` and ``on_tick`` (hook lists). Caretaker is
the only caller of ``Registry.set_health``.
"""

from .caretaker import Caretaker, default_checks, enter_safe_mode, plain_reason, safe_mode_steps

__all__ = ["Caretaker", "default_checks", "enter_safe_mode", "plain_reason", "safe_mode_steps"]
