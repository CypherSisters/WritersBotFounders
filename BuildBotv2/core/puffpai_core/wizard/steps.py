"""The step list: the active ``wizard`` package's ``steps``, changed by bundles (03 1.1).

The wizard package is found by its kind, in the Registry or among the
packages that ship with PuffPAI. Each installed ``bundle`` may change the
list with ``includes.wizard``: ``remove`` (never a ``required`` step),
``add`` (each with ``after``), ``preset`` (answers, e.g. a goal) and
``order``. The finished list is checked once (D36(a)): a secret whose home
is the vault, in a step placed before the ``vault`` step, refuses the list.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from .. import desktop

logger = logging.getLogger(__name__)

#: The step ids the wizard engine has code for (``engine.WizardEngine.hooks``; a test keeps the two the same).
CODED_STEPS = frozenset({"scan", "brain", "vault", "bridge", "toolsmith", "goal", "name", "look", "plan", "yes",
                         "build", "prove", "done"})
#: Step ids an older wizard package had, and what took their place (None: nothing; lane I10c moved the apps
#: to the PuffPAI store). A bot whose installed wizard is older still walks through to done (lane I14).
RETIRED_STEPS: dict[str, str | None] = {"apps": None}


def coded_only(steps: list[dict]) -> list[dict]:
    """The wizard package's own steps the engine can run. A retired id goes (its replacement, if any, is a step
    of its own in a newer package), and an id with no code goes, each with a log line, so setup never asks a
    question no step can answer."""
    out = []
    for s in steps:
        sid = s.get("id")
        if sid in RETIRED_STEPS:
            to = RETIRED_STEPS[sid]
            logger.info("wizard step %s is retired (%s); skipped", sid, f"{to} took its place" if to else "nothing")
            continue
        if sid not in CODED_STEPS:
            logger.info("wizard step %s has no code in this BuildBot; skipped", sid)
            continue
        out.append(s)
    return out


class StepListError(ValueError):
    """A step list that can't run. The message is plain words."""


def check_order(steps: list[dict]) -> None:
    """Refuse a list that asks for a vault secret before the vault exists (D36(a))."""
    ids = [s["id"] for s in steps]
    vault_at = ids.index("vault") if "vault" in ids else None
    for i, step in enumerate(steps):
        if step["id"] == "vault":
            continue
        for field in step.get("fields", []):
            if field.get("type") == "secret" and str(field.get("home", "")).startswith("vault:"):
                if vault_at is None or i < vault_at:
                    raise StepListError(f"Step {step['id']} asks for a key before the vault exists. "
                                        f"Move it after the vault step.")
    seen = set()
    for sid in ids:
        if sid in seen:
            raise StepListError(f"Two steps have the id {sid}. Each step needs its own id.")
        seen.add(sid)


def apply_bundle(steps: list[dict], change: dict) -> tuple[list[dict], dict]:
    """``(steps, preset)`` after one bundle's ``includes.wizard`` block."""
    steps = copy.deepcopy(steps)
    required = {s["id"] for s in steps if s.get("required")}
    for sid in change.get("remove") or []:
        if sid in required:
            raise StepListError(f"A bundle can't remove the {sid} step: the bot can't run without it.")
        steps = [s for s in steps if s["id"] != sid]
    for new in change.get("add") or []:
        new = {"fields": [], **copy.deepcopy(new)}
        new.setdefault("title", new["id"])
        after = new.pop("after", None)
        ids = [s["id"] for s in steps]
        at = ids.index(after) + 1 if after in ids else len(steps)
        steps.insert(at, new)
    order = change.get("order")
    if order:
        by_id = {s["id"]: s for s in steps}
        steps = [by_id[i] for i in order if i in by_id] + [s for s in steps if s["id"] not in order]
    return steps, dict(change.get("preset") or {})


def load_steps(state: Path, registry: Any = None) -> tuple[list[dict], dict, str]:
    """``(steps, preset, wizard package id)`` for this bot."""
    wizard_id = None
    if registry is not None:
        rows = registry.list(kind="wizard")
        wizard_id = rows[0]["id"] if rows else None
    if wizard_id is None:
        builtin = desktop.builtin_of_kind("wizard")
        wizard_id = builtin[0] if builtin else None
    if wizard_id is None:
        raise StepListError("No wizard package is installed, so there are no setup steps.")
    data = desktop.read_manifest(state, wizard_id, registry) or {}
    steps = coded_only(list(data.get("steps") or []))      # a bundle's own added steps are data, kept below
    if not steps:
        raise StepListError(f"The wizard package {wizard_id} has no steps.")
    preset: dict = {}
    if registry is not None:
        for row in registry.list(kind="bundle"):
            change = ((desktop.read_manifest(state, row["id"], registry) or {}).get("includes") or {}).get("wizard")
            if change:
                steps, more = apply_bundle(steps, change)
                preset.update(more)
    check_order(steps)
    return steps, preset, wizard_id
