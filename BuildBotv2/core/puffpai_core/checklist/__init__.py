"""The Checklist, "Your turn" (lane L5; Majordomo keeps it).

Interface: ``Checklist(path, bus=None, scope=None)`` with ``add(step, package, blocks=None,
trusted=False, trust=None, raised_by=None)`` (``trust``: core, owner or package; only
core raises admin steps or command checks, D49), ``add_setup_steps(package, manifest)``
(after the owner's Yes), ``list(package=None, include_closed=False)``, ``get(id)``,
``done(id, by)``, ``skip(id, by)``, ``wait_closed(id, timeout)``, and
``as_agent()`` (Majordomo's Bus handler for the ``checklist.*`` intents).
Steps use the one step shape, ``contracts/puffpai-package.schema.json#/$defs/step``;
``package`` and ``blocks`` are filled in by the Core. A ``check`` runs on Done.
Each change publishes ``checklist.changed`` (ids only).
"""

from .store import CheckScope, Checklist, ChecklistError, run_check

__all__ = ["CheckScope", "Checklist", "ChecklistError", "run_check"]
