"""The model slot as the wizard sees it (lane L5 calls these; 03 sections 1.4 and 1.5).

    zone2.models.menu(census) -> [option, ...]     strongest first, one with recommended: true
    zone2.models.fit(census)  -> {options, recommended, already_here, too_big, message, ...}
    zone2.models.state(name)  -> {state, model, reason, done_gb?, size_gb?, in_memory?}
    zone2.models.census()     -> the hardware census (run_census)

``state`` is one of ``not_downloaded``, ``loading``, ``ready``, ``timed_out``,
``error`` (D36(d)); the wizard owns the lines it shows for each. Both read the
model server named in ``settings\\model.json`` (``zone2.settings``). Nothing
here downloads or removes a model; ``runtime().pull(name)`` and
``runtime().warm(name)`` are there for the brain step to call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import catalog, census as _census
from . import settings as model_settings
from .ollama import ONLY_LINE, OllamaRuntime, only_model

_runtimes: dict[str, OllamaRuntime] = {}


def runtime(state: Optional[Path] = None) -> OllamaRuntime:
    s = model_settings.load(state)
    rt = _runtimes.get(s.address)
    if rt is None:
        rt = _runtimes[s.address] = OllamaRuntime(s.address, timeout_s=s.timeout_s)
    return rt


def installed(state: Optional[Path] = None) -> list[str]:
    try:
        return [m["name"] for m in runtime(state).models()]
    except (OSError, ValueError):
        return []


def census(data_dir: Optional[str] = None) -> dict[str, Any]:
    return _census.run_census(data_dir)


def fit(census_: dict[str, Any], installed_models: Optional[list[str]] = None,
        state: Optional[Path] = None) -> dict[str, Any]:
    here = installed(state) if installed_models is None else installed_models
    out = catalog.fit_menu(census_, here)
    only = only_model()
    if not only:
        # D132: every chat model already on this Ollama is a choice, in the catalog or not. A model that only makes
        # embeddings can't chat, and a hosted one is not local.
        known = [o.get("id") for o in out.get("options") or []]
        for name in here:
            low = str(name).lower()
            if "embed" in low or low.endswith(("cloud", "-cloud")) or any(catalog.is_installed(k, [name]) for k in known):
                continue
            out.setdefault("options", []).append({"id": name, "label": name, "installed": True, "recommended": False,
                                                  "reason": None})
        return out
    # D132: the one model she set is the whole menu, and the default, whatever else this Ollama holds.
    row = next((o for o in out.get("options") or [] if o.get("id") == only), None)
    row = {**(row or {"id": only, "label": only, "reason": None}), "recommended": True,
           "installed": catalog.is_installed(only, here)}
    return {**out, "options": [row], "too_big": [], "already_here": [row] if row["installed"] else [],
            "recommended": {"id": only, "label": row.get("label") or only, "reason": ONLY_LINE.format(only=only, model="another")}}


def menu(census_: dict[str, Any], installed_models: Optional[list[str]] = None,
         state: Optional[Path] = None) -> list[dict]:
    return fit(census_, installed_models, state)["options"]


def state(name: str, state_dir: Optional[Path] = None) -> dict:
    return runtime(state_dir).state(name)


__all__ = ["census", "fit", "installed", "menu", "runtime", "state"]
