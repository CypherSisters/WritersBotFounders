r"""Model roles: which installed model does which job (lane I17). ``<state>\settings\models.json``.

    roles.role_map(state)              -> {"chat": "qwen3.5:9b", "code": "", "vision": "", "fast": ""}
    roles.model_for(state, "code")     -> the code model, or chat's when none is set
    roles.settings_for(state, "code")  -> ModelSettings for that model (its own profile: think, context, JSON)
    roles.set_role(state, role, model, installed)
    roles.rows(census, installed)      -> every catalog row with its roles, fit and download size
    roles.offer(state, role)           -> what a plan card offers for a role (lane I17 item 4), or None

The roles: ``chat`` (the bot's one brain; it stays in ``settings\model.json``, which the wizard's brain step
writes, so the old ``GET /v1/model`` reads the same model), ``code`` (writing and fixing code: Toolsmith's
turns on this computer), ``vision`` (reading pictures: ``POST /v1/brain/chat`` with images) and ``fast``
(small and quick: kept for later, nothing uses it yet). A role with no model of its own uses chat's.

**Local models only.** A role takes only a model that runs on this computer: a catalog row, or the model
picked in the wizard's brain step ("Use another model"), and never a hosted one. A name ending in ``-cloud``
or ``:cloud``, a name with a slash, a backslash, ``@`` or an address in it, and a model Ollama's own
``/api/show`` reports as remote are refused (:func:`local_problem`). Only Toolsmith may use a hosted model,
on its own path with her Yes.

**Downloads, as found (lane I17).** Ollama is its own program and downloads a model itself, from
``registry.ollama.ai`` over HTTPS (port 443), which sends the files from its own download sites. The Core
only asks the local Ollama (``POST /api/pull`` on 127.0.0.1) to start it. That traffic never passes the
Bridge: the Bridge's proxy is for packages, and its watch looks only at package Job Objects, so the Bridge
can't open or close it. So the Core asks first, on a Yes card that names the model, its size and that host,
and says plainly that the download goes around the Bridge. No Bridge grant is made for it.
"""

from __future__ import annotations

import dataclasses
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from .. import jsonfile
from . import catalog
from . import settings as model_settings

ROLES = ("chat", "code", "vision", "fast")
#: Each role in a plan card's or a page's words: "a coding model", "the picture model".
ROLE_WORDS = {"chat": "chat", "code": "coding", "vision": "picture", "fast": "small and fast"}
#: What a model of each role is for, on its download card: "a local model for reading pictures".
FOR_WORDS = {"chat": "chat", "code": "writing code", "vision": "reading pictures", "fast": "small, quick jobs"}
NOT_LOCAL = "{model} is not a model that runs on this computer, so no role can use it. Only Toolsmith may use a hosted model."
NOT_LISTED = "{model} is not in the list. Pick a model from the list, or the one you picked in setup."
NOT_CHECKED = "BuildBot could not check that {model} runs on this computer, so it was not used."
FILE = "models.json"
PULL_HOST = "registry.ollama.ai"
PULL_KIND = "model_pull"


class RoleError(ValueError):
    """A role change that is refused, in plain words."""


def path(state: Path | str) -> Path:
    return Path(state) / "settings" / FILE


def _saved(state: Path | str) -> dict[str, str]:
    data = jsonfile.read(path(state), {})
    got = data.get("roles") if isinstance(data, dict) else None
    return {r: str(got.get(r) or "") for r in ROLES if r != "chat"} if isinstance(got, dict) else {}


def role_map(state: Path | str) -> dict[str, str]:
    """Every role and its model ("" when none is set). Chat's is the brain in ``settings\\model.json``."""
    saved = _saved(state)
    out = {"chat": model_settings.load(Path(state)).model}
    out.update({r: saved.get(r, "") for r in ROLES if r != "chat"})
    return out


def model_for(state: Path | str, role: str) -> str:
    """The model that does *role*: its own when one is set (and its name is a local one), else chat's."""
    got = role_map(state)
    own = got.get(role) or ""
    return own if own and not name_problem(own) else got["chat"]


def name_problem(model: str) -> str:
    """The plain reason *model*'s name is not a local Ollama model, or "" (no model server is asked)."""
    name = str(model or "").strip()
    low = name.lower()
    if not name:
        return "Type a model name."
    if low.endswith("-cloud") or low.endswith(":cloud") or any(c in name for c in "/\\@") or "://" in name \
            or any(c.isspace() for c in name):
        return NOT_LOCAL.format(model=name)
    return ""


def is_remote(info: Any) -> bool:
    """True when Ollama's ``/api/show`` answer says the model runs somewhere else (a hosted model)."""
    return isinstance(info, dict) and bool(info.get("remote_host") or info.get("remote_model"))


def local_problem(state: Path | str, model: str, show: Optional[Callable[[str], Any]] = None,
                  listed_only: bool = True) -> str:
    """The plain reason *model* can't do a role, or "": a local name, in the catalog or picked in the brain
    step (*listed_only*), and not remote by Ollama's own ``/api/show``."""
    said = name_problem(model)
    if said:
        return said
    if listed_only:
        listed = {str(m.get("id")) for m in catalog.load_catalog().get("models") or [] if isinstance(m, dict)}
        other = str(jsonfile.read(Path(state) / "settings" / "model.json", {}).get("other") or "")
        if model not in listed and model != other:
            return NOT_LISTED.format(model=model)
    if show is None:
        from . import models
        show = models.runtime(Path(state)).show
    try:
        info = show(model)
    except (OSError, ValueError):
        return NOT_CHECKED.format(model=model)
    return NOT_LOCAL.format(model=model) if is_remote(info) else ""


def settings_for(state: Path | str, role: str) -> model_settings.ModelSettings:
    """The model settings for *role*. Another model than chat's gets its own profile's context and tool budget
    (the owner's context and budget are chat's), so think-off and JSON asks follow that model."""
    s = model_settings.load(Path(state))
    model = model_for(state, role)
    if model == s.model:
        return s
    return dataclasses.replace(s, model=model, context=None, tool_budget=None)


def set_role(state: Path | str, role: str, model: str, installed: list[str],
             show: Optional[Callable[[str], Any]] = None) -> dict[str, str]:
    """Set *role* to an installed local *model* ("" takes a role back to chat's). Chat always has a model.
    A role already set to that model is not written again."""
    if role not in ROLES:
        raise RoleError("A role is chat, code, vision or fast.")
    model = str(model or "").strip()
    if not model:
        if role == "chat":
            raise RoleError("Chat always has a model. Pick another installed model for it instead.")
    else:
        said = name_problem(model)
        if said:
            raise RoleError(said)
        if not catalog.is_installed(model, installed):
            raise RoleError(f"{model} is not on this computer yet. Add it first.")
        said = local_problem(state, model, show)
        if said:
            raise RoleError(said)
    if role_map(state).get(role, "") == model:
        return role_map(state)                 # set already: nothing is written twice
    if role == "chat":
        model_settings.save(Path(state), model=model)
    else:
        with jsonfile.LOCK:
            data = jsonfile.read(path(state), {})
            data = data if isinstance(data, dict) else {}
            kept = data.get("roles") if isinstance(data.get("roles"), dict) else {}
            kept = {k: v for k, v in kept.items() if k in ROLES and k != "chat"}
            if model:
                kept[role] = model
            else:
                kept.pop(role, None)
            data["roles"] = kept
            jsonfile.write(path(state), data)
    return role_map(state)


# -- the catalog, per role -----------------------------------------------------------------------------

def census(state: Path | str) -> dict[str, Any]:
    """This computer's census: the wizard's scan (``settings\\hardware.json``), else a fresh one."""
    got = jsonfile.read(Path(state) / "settings" / "hardware.json", None)
    if isinstance(got, dict) and (got.get("memory") or {}).get("total_gb") is not None:
        return got
    from . import census as census_mod
    return census_mod.run_census(os.environ.get("OLLAMA_MODELS") or str(Path.home() / ".ollama" / "models"))


def rows(census_: dict[str, Any], installed: list[str], cat: Optional[dict] = None) -> list[dict]:
    """Every catalog row with its roles, ``fit`` (comfortable, tight or None), ``fits`` (its RAM fits and,
    when not downloaded, the free disk holds it with room) and its download size. Catalog order."""
    cat = cat or catalog.load_catalog()
    ram = catalog._num((census_.get("memory") or {}).get("total_gb"))
    free = catalog._num((census_.get("disk") or {}).get("free_gb"))
    out = []
    for m in cat.get("models") or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        here = catalog.is_installed(str(m["id"]), installed)
        fit = catalog._fit(m, ram) if ram is not None else None
        disk = catalog._num(m.get("disk_gb")) or 0.0
        room = here or free is None or free >= disk * catalog.DISK_MARGIN
        out.append({"id": str(m["id"]), "label": str(m.get("label") or m["id"]), "roles": catalog.roles_of(m),
                    "params": m.get("params"), "params_b": catalog._num(m.get("params_b")),
                    "strength": str(m.get("strength") or ""), "min_ram_gb": m.get("min_ram_gb"),
                    "rec_ram_gb": m.get("rec_ram_gb"), "disk_gb": m.get("disk_gb"), "tools": bool(m.get("tools")),
                    "installed": here, "fit": fit, "fits": fit is not None and room,
                    "download": "Already downloaded" if here else f"{m.get('disk_gb')} GB download"})
    return out


def for_role(role: str, all_rows: list[dict]) -> list[dict]:
    """The rows that fit this computer for *role*, best first: the ones already here, then the comfortable fits
    (largest first), then the tight ones (smallest first)."""
    mine = [r for r in all_rows if role in r["roles"] and r["fits"]]

    def key(r: dict) -> tuple:
        size = r.get("params_b") or 0.0
        if r["installed"]:
            return (0, -size)
        return (1, -size) if r["fit"] == "comfortable" else (2, size)
    return sorted(mine, key=key)


def best(role: str, all_rows: list[dict]) -> Optional[dict]:
    got = for_role(role, all_rows)
    return got[0] if got else None


def offer(state: Path | str, role: str, census_: Optional[dict] = None,
          installed: Optional[list[str]] = None) -> Optional[dict]:
    """What a plan card offers for *role* (not chat): None when the role has its own model already.

    ``{role, model, label, disk_gb, installed, fits, line}``. ``fits`` False: nothing in the catalog fits this
    computer for that role, said plainly, with nothing to tick."""
    if role not in ROLES or role == "chat" or _saved(state).get(role):
        return None
    if installed is None:
        from . import models
        installed = models.installed(Path(state))
    pick = best(role, rows(census_ if census_ is not None else census(state), installed))
    words = ROLE_WORDS[role]
    if pick is None:
        return {"role": role, "model": "", "label": "", "disk_gb": None, "installed": False, "fits": False,
                "line": f"A {words} model would help this bot, but none we list fits this computer."}
    if pick["installed"]:
        line = f"Use {pick['label']} as the {words} model: it is on this computer already. Tick to use it."
    else:
        line = (f"Add a {words} model: {pick['label']}, {pick['disk_gb']} GB download, fits your computer. "
                "Tick to add it.")
    return {"role": role, "model": pick["id"], "label": pick["label"], "disk_gb": pick["disk_gb"],
            "installed": pick["installed"], "fits": True, "line": line}


# -- does the goal need a model beyond chat? (one small local JSON ask while planning) ----------------------

NEEDS_SYSTEM = (
    "You decide whether a bot needs a special local model besides its chat model, from the bot's goal and its "
    "jobs. Two kinds exist. code: the bot's main work is writing, reading or fixing program code. vision: the bot "
    "must look at pictures, photos, scans or screenshots to do its jobs. Say true only when the goal or a job "
    "clearly needs it; most bots need neither. "
    'Answer {"code": true or false, "vision": true or false}.')
#: The roles a plan may offer a model for (fast is kept for later).
OFFERED = ("code", "vision")


def needs(ask_json: Optional[Callable[[str, str], Any]], goal: str, jobs: list[dict]) -> list[str]:
    """The roles beyond chat the brain says this goal needs (``code``, ``vision``); [] when it was not asked or
    answered something else (never a veto: the plan goes on without an offer)."""
    if ask_json is None:
        return []
    import json
    user = json.dumps({"goal": str(goal or "")[:600],
                       "jobs": [{"name": str(j.get("name") or "")[:80], "does": str(j.get("does") or "")[:300]}
                                for j in (jobs or [])[:12] if isinstance(j, dict)]}, ensure_ascii=False)
    try:
        got = ask_json(NEEDS_SYSTEM, user)
    except Exception:  # noqa: BLE001 - no answer: no offer
        return []
    if not isinstance(got, dict):
        return []
    return [r for r in OFFERED if got.get(r) is True]


# -- a download, after its own Yes ------------------------------------------------------------------------

def pull_card(row: dict) -> tuple[dict, dict]:
    """``(scope, card)`` for one model's download Yes card: the model, its size and the host, said plainly."""
    words = " and ".join(FOR_WORDS[r] for r in row.get("roles") or [] if r in FOR_WORDS) or "chat"
    card = {"title": f"Download {row['label']}?", "model": row["id"], "label": row["label"],
            "size_gb": row.get("disk_gb"), "host": PULL_HOST,
            "lines": [f"What it is: {row['label']} ({row['id']}), a local model for {words}.",
                      f"Download: about {row.get('disk_gb')} GB, from {PULL_HOST} and Ollama's download sites.",
                      "Ollama downloads it itself, so this download goes around the Bridge. Only the model's name "
                      "is asked for.",
                      "It stays on this computer and runs here. Nothing leaves your computer without your consent."]}
    return {"kind": PULL_KIND, "target": f"model:{row['id']}"[:128]}, card


#: Downloads asked for from the Developer page: state folder -> model -> {state, line, consent_id}.
_pulls: dict[str, dict[str, dict]] = {}
_lock = threading.Lock()


def pulls(state: Path | str) -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _pulls.get(str(Path(state)), {}).items()}


def note_pull(folder: Path | str, model: str, **fields: Any) -> None:
    """Keep how one download is going (``state``, ``line``, ``consent_id``) for ``GET /v1/models``."""
    with _lock:
        _pulls.setdefault(str(Path(folder)), {}).setdefault(model, {}).update(fields)


#: One download per model at a time: a second ask while one runs waits for it and gets its answer.
_inflight: dict[str, dict] = {}
CANCELLED = "you cancelled it"


def pull_now(state: Path | str, model: str, label: str = "") -> tuple[bool, str]:
    """Ask the local Ollama to download *model* (blocking). ``(done, why not)``. Call only after its Yes.
    Safe to ask twice: a model already on this computer is not downloaded again, and a second ask while a
    download of it runs waits for that one."""
    from . import models
    key = f"{Path(state)}|{model}"
    with _lock:
        cur = _inflight.get(key)
        mine = cur is None
        if mine:
            cur = _inflight[key] = {"event": threading.Event(), "result": (False, "the download did not finish")}
    if not mine:
        cur["event"].wait()
        return cur["result"]
    try:
        cur["result"] = _pull_once(state, model, label or model)
        return cur["result"]
    finally:
        with _lock:
            _inflight.pop(key, None)
        cur["event"].set()


def _pull_once(state: Path | str, model: str, name: str) -> tuple[bool, str]:
    from . import models
    rt = models.runtime(Path(state))
    try:
        here = [str(m.get("name") or "") for m in rt.models()]
    except (OSError, ValueError):
        here = []
    if catalog.is_installed(model, here):
        note_pull(state, model, state="done", line=f"{name} is downloaded.")
        return True, ""
    note_pull(state, model, state="downloading", line=f"Downloading {name}.")     # GET /v1/models shows it
    done = bool(rt.pull(model))
    if done:
        note_pull(state, model, state="done", line=f"{name} is downloaded.")
        return True, ""
    why = str((rt.state(model) or {}).get("reason") or "the download did not finish")
    if (pulls(state).get(model) or {}).get("state") == "cancelled":
        return False, CANCELLED
    note_pull(state, model, state="failed", line=f"{name} was not downloaded: {why}.")
    return False, why


def cancel_pull(state: Path | str, model: str, consent: Any = None) -> bool:
    """Stop a download asked for here (lane I17 fix): a card still waiting is closed; a download running is
    stopped (Ollama's stream is closed). It is marked cancelled, and no role is set. False when nothing runs."""
    from . import models
    rec = pulls(state).get(model) or {}
    if rec.get("state") not in ("asking", "downloading"):
        return False
    note_pull(state, model, state="cancelled", line=f"{rec.get('label') or model} was not downloaded: {CANCELLED}.")
    if rec.get("consent_id") and consent is not None:
        try:
            consent.expire(rec["consent_id"], "You cancelled this download.")
        except Exception:  # noqa: BLE001 - the card closes on its own after 10 minutes anyway
            pass
    stop = getattr(models.runtime(Path(state)), "cancel", None)
    if callable(stop):
        stop(model)
    return True


def pull_after_yes(state: Path | str, row: dict, consent: Any, consent_id: str, *, wait_s: float = 11 * 60,
                   notify: Optional[Callable[[str, str], None]] = None,
                   pull: Optional[Callable[[str], tuple[bool, str]]] = None) -> str:
    """Wait for the download card's answer; after a live Yes for exactly that card, download. The final state:
    ``done``, ``failed``, ``declined`` or ``no_answer``. Progress is kept for ``GET /v1/models``; *notify*
    (title, text) tells the owner how it ended."""
    from ..consent import card_sha
    model, label = row["id"], row["label"]
    rec = consent.wait(consent_id, timeout_s=wait_s)
    if (pulls(state).get(model) or {}).get("state") == "cancelled":
        return "cancelled"
    if rec is not None and rec.get("status") == "pending":
        consent.expire(consent_id, f"The Yes card to download {label} got no answer in time, so nothing was "
                                   "downloaded.")
        rec = consent.get(consent_id)
    status = (rec or {}).get("status")
    shown = (rec or {}).get("card") or {}
    live = status == "granted" and consent.granted(consent_id) is not None \
        and rec.get("card_sha") == card_sha(shown) and shown.get("model") == model
    if not live:
        final = "declined" if status == "declined" else "no_answer"
        note_pull(state, model, state=final, line=f"{label} was not downloaded.")
        return final
    ok, why = (pull or (lambda m: pull_now(state, m, label)))(model)
    if why == CANCELLED:
        return "cancelled"
    if ok:
        note_pull(state, model, state="done", line=f"{label} is downloaded. Pick what it is used for on the "
                                                  "Developer page.")
        if notify:
            notify(f"{label} is downloaded", f"Pick what {label} is used for on the Developer page.")
        return "done"
    note_pull(state, model, state="failed", line=f"{label} was not downloaded: {why}.")
    if notify:
        notify(f"{label} was not downloaded", f"{why}.")
    return "failed"


# -- what each role's model has done (lane I19b) --------------------------------------------------------------
#: ``<state>\settings\model_use.json``: per role and model, how many pictures (or asks) it has read, when last
#: and for which app. Counts only, never a picture or a word of its answer.
USE_FILE = "model_use.json"
_use_lock = threading.Lock()


def note_use(state: Path | str, role: str, model: str, by: str = "", n: int = 1) -> None:
    """Count *n* uses of *model* in *role* (for example 2 pictures read for the app *by*)."""
    if role not in ROLES or not model:
        return
    from datetime import datetime, timezone
    p = Path(state) / "settings" / USE_FILE
    with _use_lock:
        data = jsonfile.read(p, {})
        data = data if isinstance(data, dict) else {}
        per = data.get(role) if isinstance(data.get(role), dict) else {}
        data[role] = per
        rec = per.get(model) if isinstance(per.get(model), dict) else {}
        per[model] = {"count": int(rec.get("count") or 0) + max(1, int(n)),
                      "last_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "last_by": str(by or "")[:80]}
        jsonfile.write(p, data)


def used(state: Path | str) -> dict[str, dict]:
    """Per role: the model doing it now, whether it is the role's own (not chat's), and what it has done
    (``count``, ``last_at``, ``last_by``; 0 and "" when nothing used it yet)."""
    data = jsonfile.read(Path(state) / "settings" / USE_FILE, {})
    data = data if isinstance(data, dict) else {}
    own = role_map(state)
    out = {}
    for role in ROLES:
        model = model_for(state, role)
        per = data.get(role) if isinstance(data.get(role), dict) else {}
        rec = per.get(model) if isinstance(per.get(model), dict) else {}
        out[role] = {"model": model, "own": bool(own.get(role)) and own.get(role) == model,
                     "count": int(rec.get("count") or 0), "last_at": str(rec.get("last_at") or ""),
                     "last_by": str(rec.get("last_by") or "")}
    return out


__all__ = ["CANCELLED", "FOR_WORDS", "cancel_pull", "is_remote", "local_problem", "name_problem", "FILE", "NEEDS_SYSTEM", "OFFERED", "PULL_HOST", "PULL_KIND", "ROLES", "ROLE_WORDS", "RoleError", "best",
           "census", "for_role", "model_for", "needs", "note_pull", "offer", "path", "pull_after_yes", "pull_card",
           "pull_now", "pulls", "role_map", "rows", "set_role", "settings_for", "note_use", "used", "USE_FILE"]
