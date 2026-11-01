# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted)
r"""Toolsmith's settings: ``settings\toolsmith.json`` in the bot folder.

Two parts, both data (03 step 5, 03 3.3):
- the owner's choice of model: ``{"where": "local"}`` or ``{"where": "cloud",
  "provider", "model", "cost_cap_usd", "consent": {"sha", "at", "by"}}``. No
  file, junk, or a cloud choice without her consent sha all read as local.
- the try budgets and command timing (v1 values as starting data, tunable).

Budgets and timing from v1 ``toolsmith_run.py``
(``STEPS_PER_APP`` :55, ``APP_MINUTES`` :57, ``REPEATS_TO_END`` :59, ``STEPS_PER_MAKE`` :61,
``MAKE_MINUTES`` :62, ``COMMAND_WAIT_S`` :68, ``READS_PER_TRY`` :77, ``LONG_COMMAND_S`` :79,
``BUILD_COMMAND_S`` and ``BUILD_QUIET_S`` :98-99).
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FILE = "toolsmith.json"

DEFAULT_CAP_USD = 5.0     # D43

DEFAULTS: dict[str, Any] = {
    "where": "local",
    "budgets": {
        "adopt_steps": 20, "adopt_minutes": 12,
        "make_steps": 30, "make_minutes": 15,
        "reads_per_try": 15, "repeats_to_end": 3,
        "candidates_per_job": 5,
    },
    "timing": {
        "command_wait_s": 120, "long_command_s": 600,
        "build_command_s": 900, "build_quiet_s": 300,
        "page_wait_s": 240, "vault_wait_s": 7200, "poll_s": 5,
        "plan_wait_s": 600,
    },
}


def path(state: Path | str) -> Path:
    return Path(state) / "settings" / FILE


def _merge(base: dict, over: Any) -> dict:
    out = copy.deepcopy(base)
    if not isinstance(over, dict):
        return out
    for key, value in over.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = {**out[key], **{k: v for k, v in value.items()
                                       if isinstance(v, (int, float)) and not isinstance(v, bool)}}
        elif key not in ("budgets", "timing"):
            out[key] = value
    return out


def read(state: Path | str) -> dict:
    """The settings, defaults filled in. A cloud choice that is incomplete reads as local."""
    try:
        raw = json.loads(path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    got = _merge(DEFAULTS, raw)
    if got.get("where") == "cloud":
        consent = got.get("consent") if isinstance(got.get("consent"), dict) else {}
        if not (got.get("provider") and consent.get("sha")):
            got["where"] = "local"
        try:
            cap = float(got.get("cost_cap_usd", DEFAULT_CAP_USD))
        except (TypeError, ValueError):
            cap = DEFAULT_CAP_USD
        got["cost_cap_usd"] = cap if cap >= 0 else DEFAULT_CAP_USD
    else:
        got["where"] = "local"
    return got


def write(state: Path | str, choice: dict) -> dict:
    """Save the owner's model choice (budgets and timing are kept as they are)."""
    p = path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        current = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    for key in ("where", "provider", "model", "cost_cap_usd", "consent"):
        current.pop(key, None)
    current.update(choice)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return read(state)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = ["DEFAULTS", "DEFAULT_CAP_USD", "now", "path", "read", "write"]
