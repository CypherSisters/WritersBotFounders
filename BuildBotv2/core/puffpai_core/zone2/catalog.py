# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/model_catalog.py (adapted)
# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/model_profile.py (adapted)
"""The brain menu and the model profiles (03 sections 1.5, 01 "Zone 2").

Data lives in ``zone2/data/model_catalog.json`` (the RAM-first ladder) and
``zone2/data/model_profiles.json`` (context, tool budget, thinking). This
module only reads them.

The recommender (D36(e), 03 section 1.5), as data rules:
1. **A benchmarked board wins.** When the census really is a ``silicon``
   entry (the identity check, :func:`census_is_this_board`), a tool model of
   9B or more carries ``benchmarks.<that silicon id>``, total RAM is at least the row's
   ``rec_ram_gb`` and free disk is at least ``disk_gb`` x 1.2 (an installed
   model needs no download), that model is Recommended; the largest
   ``params_b`` wins. Smaller models already downloaded are listed under it.
   This works for any silicon entry, not only the Jetson.
2. **Otherwise** v1's tool rule: an installed 9B-or-larger tool model, else
   the smallest 9B-or-larger tool model that fits, else the smallest tool
   model that fits (:data:`TOOL_MODEL_MIN_B`).
3. The reason line always shows the download size, or "Already downloaded".

Never extrapolate: a board with no measured row falls through to rule 2, and
benchmark numbers show only on the board they were measured on.

Note on "9B or more" in rule 1 (lane L2, 10-22): 03 section 1.5 does not say
it, but its own test T-PB005 case (2) expects rule 2 on a Jetson with 20 GB
free, and the catalog's ``qwen2.5:7b-instruct`` row is also benchmarked on the
Jetson and would win rule 1 there. Keeping rule 1 to the size rule 2 already
asks for makes the rule and the test agree. Flagged for the docs owner.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

DATA = Path(__file__).resolve().parent / "data"

#: Billions of parameters from which a model uses tools well (v1 model_catalog.py:302).
TOOL_MODEL_MIN_B = 9.0
#: Free disk must cover the download plus this share (D36(e)).
DISK_MARGIN = 1.2

_HARD_DEFAULT: dict[str, Any] = {
    "routing_tier": "guided",
    "supports": {"planning": False, "json_mode": True, "thinking": False},
    "budgets": {"num_ctx": 4096, "num_predict": 512, "plan_ctx": 4096},
    "limits_note": "We have not tested this brain, so it is set up to be told one step at a time.",
    "basis": "conservative-default",
    "tool_budget": 16,
    "needs_thinking": False,
    "vision": False,
}

NO_TOOLS_NOTE = ("None of the models that fit this computer can use tools, so chat will answer "
                 "without using what you add.")


# -- loading ----------------------------------------------------------------

@lru_cache(maxsize=4)
def _read(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def load_catalog() -> dict[str, Any]:
    return _read("model_catalog.json")


def load_profiles() -> dict[str, Any]:
    return _read("model_profiles.json")


# -- profiles (v1 model_profile.get_profile :161) ----------------------------

def get_profile(model_id: str, profiles: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Exact id, then the longest id prefix (a re-tagged variant), then ``_default``.

    An inherited profile is never ``measured``: the variant is not what was benched.
    """
    table = (profiles or load_profiles()).get("profiles") or {}
    wanted = (model_id or "").strip().lower()
    named = {str(k).lower(): v for k, v in table.items() if k != "_default" and isinstance(v, dict)}
    if wanted in named:
        return {**_HARD_DEFAULT, **named[wanted]}
    if wanted:
        candidates = [k for k in named if wanted.startswith(k)]
        if candidates:
            best = max(candidates, key=len)
            return {**_HARD_DEFAULT, **named[best], "basis": "conservative-default", "inherited_from": best}
    fallback = table.get("_default")
    return {**_HARD_DEFAULT, **fallback} if isinstance(fallback, dict) else dict(_HARD_DEFAULT)


# -- board identity (v1 model_profile._census_is_this_board :261) -------------

def _board_tokens(silicon_id: str, label: str) -> set[str]:
    words = set(re.split(r"[^a-z0-9]+", f"{silicon_id} {label}".lower()))
    return {w for w in words if w and not re.fullmatch(r"\d+(gb|mb|tb)?", w)}


def census_is_this_board(census: dict[str, Any], silicon_id: str, board: dict[str, Any]) -> bool:
    """True only when this machine really is the board a number was measured on."""
    if str(census.get("hardware_class", "")) != str(board.get("hardware_class")):
        return False
    soc = census.get("soc") or {}
    accel = census.get("accelerator") or {}
    cpu = census.get("cpu") or {}
    haystack = " ".join(str(v or "") for v in (
        soc.get("model"), accel.get("name"), accel.get("kind"), cpu.get("model"))).lower()
    tokens = _board_tokens(silicon_id, str(board.get("label") or ""))
    return bool(tokens) and all(t in haystack for t in tokens)


def matched_boards(census: dict[str, Any], catalog: dict[str, Any]) -> dict[str, dict]:
    return {sid: board for sid, board in (catalog.get("silicon") or {}).items()
            if isinstance(board, dict) and census_is_this_board(census, sid, board)}


# -- fitting (v1 model_catalog :123-) ---------------------------------------

def _num(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _fit(model: dict[str, Any], total_ram_gb: float) -> Optional[str]:
    min_ram = _num(model.get("min_ram_gb")) or 0.0
    rec_ram = _num(model.get("rec_ram_gb")) or min_ram
    if total_ram_gb < min_ram:
        return None
    return "comfortable" if total_ram_gb >= rec_ram else "tight"


def roles_of(model: dict[str, Any]) -> list[str]:
    """What a catalog row is for (``chat``, ``code``, ``vision``, ``fast``); a row with none is a chat row."""
    got = model.get("roles")
    return [str(r) for r in got] if isinstance(got, list) and got else ["chat"]


def is_installed(model_id: str, installed: list[str]) -> bool:
    """Ollama names ``x`` and ``x:latest`` the same model."""
    want = model_id.strip().lower()
    names = {n.strip().lower() for n in installed}
    return want in names or (":" not in want and f"{want}:latest" in names)


def _download(option: dict) -> str:
    return "Already downloaded" if option.get("installed") else f"{option.get('disk_gb')} GB download"


def _tool_pick(options: list[dict]) -> Optional[tuple[dict, str]]:
    """v1 ``_tool_pick`` :316: ``(option, kind)`` or None when no fitting model calls tools."""
    tools = [o for o in options if o.get("tools")]
    if not tools:
        return None

    def size(o: dict) -> float:
        return o.get("params_b") or 0.0
    big = [o for o in tools if size(o) >= TOOL_MODEL_MIN_B]
    if big:
        here = [o for o in big if o["installed"]]
        return (max(here, key=size), "big_installed") if here else (min(big, key=size), "big")
    here = [o for o in tools if o["installed"]]
    return (max(here, key=size), "small_installed") if here else (min(tools, key=size), "small")


def _tool_reason(kind: str, pick: dict, ram: float) -> str:
    room = "" if pick["fit"] == "comfortable" else " It is a tight fit, so it may be slow."
    head = f"Recommended: {pick['label']} ({_download(pick)})."
    if kind == "big_installed":
        return f"{head} It is on this computer already and big enough (9B or more) to use tools well.{room}"
    if kind == "big":
        return f"{head} It fits your {ram:g} GB and is big enough (9B or more) to use tools well.{room}"
    if kind == "small_installed":
        return (f"{head} It is on this computer already and still uses tools. A 9B model would use "
                f"them better, but none fits here.{room}")
    return (f"{head} A 9B model does not fit this computer, so this is the smallest one that "
            f"still uses tools.{room}")


def _benchmark_pick(options: list[dict], models: dict[str, dict], boards: dict[str, dict],
                    ram: float, free_disk: Optional[float]) -> Optional[tuple[dict, dict]]:
    """Rule 1 (D36(e)): ``(option, board)`` or None."""
    found = []
    for o in options:
        row = models[o["id"]]
        if not o.get("tools") or (o.get("params_b") or 0) < TOOL_MODEL_MIN_B:
            continue                # see the module note: rule 1 never undercuts rule 2's size floor
        marks = row.get("benchmarks") or {}
        for sid, board in boards.items():
            if not isinstance(marks.get(sid), dict):
                continue
            rec_ram = _num(row.get("rec_ram_gb"))
            disk = _num(row.get("disk_gb")) or 0.0
            if rec_ram is None or ram < rec_ram:
                continue
            if not o["installed"] and (free_disk is None or free_disk < disk * DISK_MARGIN):
                continue
            found.append((o, board))
    if not found:
        return None
    return max(found, key=lambda pair: pair[0].get("params_b") or 0.0)


def fit_menu(census: dict[str, Any], installed: Optional[list[str]] = None,
             catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The brain menu for this machine. Never raises.

    ``{hardware_class, total_ram_gb, free_disk_gb, options, too_big,
    recommended, already_here, message}``. ``options`` are strongest first;
    exactly one has ``recommended: true`` when anything fits.
    """
    cat = catalog or load_catalog()
    # Only chat rows: the brain step picks the bot's one chat model (a coding or picture model is added later,
    # zone2/roles.py). A row with no roles is a chat row.
    rows = [m for m in cat.get("models") or [] if isinstance(m, dict) and m.get("id") and "chat" in roles_of(m)]
    models = {str(m["id"]): m for m in rows}
    installed = list(installed or [])
    ram = _num((census.get("memory") or {}).get("total_gb"))
    free_disk = _num((census.get("disk") or {}).get("free_gb"))
    boards = matched_boards(census, cat)
    out: dict[str, Any] = {"hardware_class": census.get("hardware_class", "unknown"), "total_ram_gb": ram,
                           "free_disk_gb": free_disk, "options": [], "too_big": [], "recommended": None,
                           "already_here": [], "message": None}
    if ram is None:
        out["message"] = ("We could not read this computer's memory, so we cannot fit a brain yet. "
                          "Tell the wizard how much RAM it has.")
        return out
    for m in rows:
        fit = _fit(m, ram)
        if fit is None:
            out["too_big"].append({"id": m["id"], "label": m.get("label"), "min_ram_gb": m.get("min_ram_gb")})
            continue
        marks = m.get("benchmarks") or {}
        bench = next(({"silicon_label": boards[sid].get("label", sid), **{k: v for k, v in nums.items() if v is not None}}
                      for sid, nums in marks.items() if sid in boards and isinstance(nums, dict)), None)
        out["options"].append({
            "id": m["id"], "label": m.get("label"), "params": m.get("params"), "params_b": _num(m.get("params_b")),
            "disk_gb": m.get("disk_gb"), "strength": m.get("strength"), "fit": fit,
            "installed": is_installed(str(m["id"]), installed), "tools": bool(m.get("tools")),
            "benchmark": bench, "benchmark_note": None if bench else "Untested on hardware like yours, reports welcome.",
            "recommended": False, "reason": None,
        })
    options = out["options"]
    if not options:
        out["message"] = ("This computer is below the floor of our smallest local model. "
                          "Add memory, or use a computer with more RAM.")
        return out

    pick, reason, rule = None, "", 0
    bench = _benchmark_pick(options, models, boards, ram, free_disk)
    if bench is not None:
        pick, board = bench
        rule = 1
        reason = (f"Recommended: {pick['label']} ({_download(pick)}). It is the brain we tested on the "
                  f"{board.get('label')}, and it fits your {ram:g} GB.")
        out["already_here"] = [
            {"id": o["id"], "label": o["label"], "line": "Already here, so it starts at once"}
            for o in options if o["installed"] and o["id"] != pick["id"]
            and (o.get("params_b") or 0) < (pick.get("params_b") or 0)]
    else:
        tool = _tool_pick(options)
        if tool is not None:
            pick, kind = tool
            rule = 2
            reason = _tool_reason(kind, pick, ram)
        else:
            here = [o for o in options if o["installed"]]
            pick = next((o for o in here + options if o["fit"] == "comfortable"), (here or options)[0])
            rule = 2
            reason = f"Recommended: {pick['label']} ({_download(pick)}). {NO_TOOLS_NOTE}"
    pick["recommended"] = True
    pick["reason"] = reason
    out["recommended"] = {"id": pick["id"], "label": pick["label"], "reason": reason, "rule": rule,
                          "installed": pick["installed"], "disk_gb": pick["disk_gb"], "fit": pick["fit"]}
    return out


__all__ = ["DISK_MARGIN", "TOOL_MODEL_MIN_B", "census_is_this_board", "fit_menu", "get_profile",
           "is_installed", "load_catalog", "load_profiles", "matched_boards", "roles_of"]
