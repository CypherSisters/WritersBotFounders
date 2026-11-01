"""The "Your turn" step store: ``checklist.json`` in the bot folder (01 "Checklist").

Any package or agent can raise a step: a package through ``POST /v1/checklist``
with its own ``PUFFPAI_TOKEN`` (the step is filed under that package, and it
sees only its own), an agent through the Bus (``checklist.add`` envelopes to
Majordomo) or in process. Majordomo keeps the list.

Who may raise what (D49):
- ``core``: the Core and its agents in process (Toolsmith, the Package
  Manager raising a package's ``setup_steps`` after the owner's Yes, read
  from the approved manifest by ``add_setup_steps``). Only these may raise an
  ``admin: true`` step or a ``command`` check.
- ``owner``: an owner credential over HTTP. No admin steps, no command checks.
- ``package``: a package token, or ``pkg:<id>`` on the Bus. No admin steps,
  no command checks; a ``url`` check may reach only that package's own port
  on 127.0.0.1, and a ``file`` check only its own folders
  (``packages/<id>``, its linked folder, ``data/<id>``).

Done runs the step's ``check`` when it has one; the step stays open until the
check passes. After a Done, each function in ``on_done`` gets the closed step
(the Core's own listeners, in process: Toolsmith's host records the owner's
tick on a build step there, D60). Every stored step gets a random id (``step_...``) so no one can
guess another's step; ``asked_id`` keeps the id the raiser gave, and
``raised_by`` says who raised it (shown on the card).
"""

from __future__ import annotations

import logging
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from .. import jsonfile
from ..packages import manifest as manifest_mod

logger = logging.getLogger(__name__)

STATES = ("open", "done", "skipped")
TRUST = ("core", "owner", "package")
# Filled in by the Core; never taken from the raiser.
CORE_FIELDS = ("package", "blocks", "state", "asked_id", "raised_by", "raised_at", "closed_at", "closed_by",
               "check_failed")
NO_ADMIN = ("A package can't raise a step that runs as administrator. Admin steps come only from the setup "
            "steps you said Yes to when it was installed, or from BuildBot itself.")
NO_COMMAND = ("A package's own step may check a url or a file, not run a command. "
              "Command checks come from the Core and its agents.")
NO_ADMIN_HTTP = ("A step raised over the Core API can't run as administrator or run a command check. "
                 "Those come only from BuildBot itself.")


class ChecklistError(ValueError):
    """A refused Checklist call. The message is plain words."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _step_validator() -> Draft202012Validator:
    schema = manifest_mod.schema()
    return Draft202012Validator({"$ref": "#/$defs/step", "$defs": schema["$defs"]})


def _hidden() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a package's own check never follows a redirect
        return None


def run_check(check: dict | None, timeout: float = 30.0, own_only: bool = False) -> tuple[bool, str]:
    """``(passed, plain reason)`` for a step's ``check``. No check passes.

    ``own_only`` (a package's own step): the url check follows no redirect and uses no proxy,
    so it stays on the address that was checked when the step was raised, and the file path
    is used as stored (already resolved inside the package's folders).
    """
    if not check:
        return True, ""
    if "command" in check:
        argv = [str(a) for a in check["command"]]
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout, check=False, **_hidden())
        except FileNotFoundError:
            return False, f"{argv[0]} is not on this computer yet."
        except subprocess.TimeoutExpired:
            return False, f"{argv[0]} did not finish in {int(timeout)} seconds."
        except OSError as exc:
            return False, f"{argv[0]} could not start ({exc.strerror or exc})."
        if proc.returncode != 0:
            return False, f"{' '.join(argv)} did not work yet (exit code {proc.returncode})."
        return True, ""
    if "url" in check:
        opener = (urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect) if own_only
                  else urllib.request.build_opener())
        try:
            with opener.open(check["url"], timeout=5) as resp:  # noqa: S310 (http or https by schema)
                if resp.status < 400:
                    return True, ""
                return False, f"{check['url']} answered {resp.status}."
        except Exception as exc:  # any failure is "not yet", with the reason
            return False, f"{check['url']} did not answer ({exc.__class__.__name__})."
    if "file" in check:
        path = check["file"] if own_only else os.path.expandvars(os.path.expanduser(check["file"]))
        if os.path.exists(path):
            return True, ""
        return False, f"{path} is not there yet."
    return False, "The step's check is not one BuildBot knows."


class CheckScope:
    """Where a package's own ``url`` and ``file`` checks may reach (D49).

    ``state`` is the bot folder; ``registry`` gives the package's port and, for a
    linked package, its folder (``origin: local:<path>``).
    """

    def __init__(self, state: Path | str, registry: Any = None) -> None:
        self.state = Path(state)
        self.registry = registry

    def _row(self, package: str) -> dict | None:
        if self.registry is None:
            return None
        try:
            return self.registry.get(package)
        except Exception:   # a missing or unreadable row is "no address", never a pass
            return None

    def folders(self, package: str) -> list[Path]:
        out = [self.state / "packages" / package, self.state / "data" / package]
        origin = (self._row(package) or {}).get("origin") or ""
        if isinstance(origin, str) and origin.startswith("local:") and len(origin) > 6:
            out.append(Path(origin[6:]))
        return [p.resolve() for p in out]

    def check(self, package: str, check: dict | None) -> tuple[dict | None, str | None]:
        """``(the check to store, None)`` if allowed, else ``(None, plain reason)``."""
        if not check:
            return check, None
        if "url" in check:
            port = (self._row(package) or {}).get("port")
            parts = urllib.parse.urlsplit(str(check["url"]))
            try:
                url_port = parts.port
            except ValueError:
                url_port = None
            if not isinstance(port, int):
                return None, ("This package has no address of its own yet, so its step can't check a url. "
                              "A package's url check may reach only its own port on 127.0.0.1.")
            if parts.scheme != "http" or parts.hostname != "127.0.0.1" or url_port != port \
                    or parts.username or parts.password:
                return None, (f"A package's own step may check only its own address, "
                              f"http://127.0.0.1:{port}/, not {check['url']}.")
            return dict(check), None
        if "file" in check:
            raw = str(check["file"])
            path = Path(raw)
            if not path.is_absolute():
                path = self.state / "packages" / package / path
            try:
                resolved = path.resolve()
            except (OSError, RuntimeError):
                return None, "That file check is not a path BuildBot can read."
            if not any(resolved == f or f in resolved.parents for f in self.folders(package)):
                return None, (f"A package's own step may check a file only in its own folders "
                              f"(packages\\{package}, data\\{package}), not {raw}.")
            return {**check, "file": str(resolved)}, None
        return None, "A package's own step may check a url or a file only."


class Checklist:
    """See the module docstring."""

    def __init__(self, path: Path | str, bus: Any = None,
                 checker: Callable[..., tuple[bool, str]] = run_check,
                 scope: CheckScope | None = None) -> None:
        self.path = Path(path)
        self.bus = bus
        self.checker = checker
        self.scope = scope or CheckScope(self.path.parent)
        self._validator = _step_validator()
        self._changed = threading.Condition()
        self.on_done: list[Callable[[dict], None]] = []   # fn(step) after each Done (Core listeners only)

    # -- storage ------------------------------------------------------------
    def _load(self) -> list[dict]:
        data = jsonfile.read(self.path, {"steps": []})
        return data.get("steps", []) if isinstance(data, dict) else []

    def _save(self, steps: list[dict]) -> None:
        jsonfile.write(self.path, {"steps": steps})

    # -- reads --------------------------------------------------------------
    def list(self, package: str | None = None, include_closed: bool = False) -> list[dict]:
        steps = self._load()
        return [s for s in steps if (package is None or s["package"] == package)
                and (include_closed or s["state"] == "open")]

    def get(self, step_id: str) -> dict | None:
        return next((s for s in self._load() if s["id"] == step_id), None)

    # -- writes -------------------------------------------------------------
    def add(self, step: dict, package: str, blocks: list[str] | None = None, trusted: bool = False,
            trust: str | None = None, raised_by: str | None = None) -> dict:
        """File a step for *package*.

        ``trust`` is ``core``, ``owner`` or ``package`` (module docstring);
        ``trusted=True`` is short for ``core``. Default: ``package``.
        """
        trust = trust or ("core" if trusted else "package")
        if trust not in TRUST:
            raise ValueError(f"unknown trust {trust!r}")
        if not isinstance(step, dict):
            raise ChecklistError("A step is one JSON object in the one step shape.")
        step = {k: v for k, v in step.items() if k not in CORE_FIELDS}
        errors = sorted(self._validator.iter_errors(step), key=lambda e: list(map(str, e.absolute_path)))
        if errors:
            raise ChecklistError(manifest_mod._plain(errors[0]))
        check = step.get("check") or {}
        if trust == "package":
            if step.get("admin"):
                raise ChecklistError(NO_ADMIN)
            if "command" in check:
                raise ChecklistError(NO_COMMAND)
            if check:
                scoped, reason = self.scope.check(package, check)
                if reason:
                    raise ChecklistError(reason)
                step["check"] = scoped
        elif trust == "owner" and (step.get("admin") or "command" in check):
            raise ChecklistError(NO_ADMIN_HTTP)
        who = raised_by or (f"pkg:{package}" if trust == "package" else package if trust == "core" else "owner")
        with jsonfile.LOCK:
            steps = self._load()
            same = next((s for s in steps if s["package"] == package and s.get("asked_id") == step["id"]
                         and s["state"] == "open" and s.get("raised_by") == who), None)
            if same is not None:                # raised again while open: the same card
                return same
            taken = {s["id"] for s in steps}
            sid = "step_" + secrets.token_urlsafe(12)
            while sid in taken:
                sid = "step_" + secrets.token_urlsafe(12)
            stored = {**step, "id": sid, "asked_id": step["id"], "package": package, "raised_by": who,
                      "blocks": list(blocks or []), "state": "open", "raised_at": _now(),
                      "closed_at": None, "closed_by": None, "check_failed": None}
            steps.append(stored)
            self._save(steps)
        self._publish(stored, "added")
        return stored

    def add_setup_steps(self, package: str, manifest: dict, blocks: list[str] | None = None) -> list[dict]:
        """The Package Manager, after the owner's Yes: raise the ``setup_steps`` of the manifest she approved.

        The caller passes the manifest the Yes was given for (the installed
        package's own ``puffpai.json``), never a request body. These steps may be
        ``admin: true``; each one still needs her PIN to run
        (``POST /v1/checklist/<id>/run-admin``).
        """
        if not isinstance(manifest, dict) or manifest.get("id") != package:
            raise ChecklistError("Those setup steps are not from this package's manifest.")
        return [self.add(dict(s), package, blocks, trust="core", raised_by=f"setup_steps:{package}")
                for s in manifest.get("setup_steps") or []]

    def done(self, step_id: str, by: str = "owner") -> tuple[bool, dict, str]:
        """``(ok, step, reason)``. Runs the check first; a failed check leaves it open."""
        step = self._need(step_id)
        if step["state"] != "open":
            return True, step, ""
        check = step.get("check")
        if str(step.get("raised_by", "")).startswith("pkg:") and check:
            check, refused = self.scope.check(step["package"], check)   # its port may have changed
            if refused:
                step = self._set(step_id, check_failed=refused)
                return False, step, refused
            ok, reason = self.checker(check, own_only=True)
        else:
            ok, reason = self.checker(check)
        if not ok:
            step = self._set(step_id, check_failed=reason)
            return False, step, reason
        step = self._set(step_id, state="done", closed_at=_now(), closed_by=by, check_failed=None)
        self._publish(step, "done")
        for fn in list(self.on_done):
            try:
                fn(dict(step))
            except Exception:  # noqa: BLE001 - a listener's bug never undoes her Done
                logger.exception("checklist: an on_done listener failed")
        return True, step, ""

    def skip(self, step_id: str, by: str = "owner") -> dict:
        step = self._need(step_id)
        if step["state"] != "open":
            return step
        step = self._set(step_id, state="skipped", closed_at=_now(), closed_by=by)
        self._publish(step, "skipped")
        return step

    def wait_closed(self, step_id: str, timeout: float | None = None) -> str:
        """Block until the step is done or skipped (Toolsmith pauses in place). Returns its state."""
        with self._changed:
            self._changed.wait_for(lambda: (self.get(step_id) or {}).get("state") != "open", timeout)
        return (self.get(step_id) or {}).get("state", "missing")

    def _need(self, step_id: str) -> dict:
        step = self.get(step_id)
        if step is None:
            raise KeyError(step_id)
        return step

    def _set(self, step_id: str, **changes: Any) -> dict:
        with jsonfile.LOCK:
            steps = self._load()
            for s in steps:
                if s["id"] == step_id:
                    s.update(changes)
                    self._save(steps)
                    return s
        raise KeyError(step_id)

    def _publish(self, step: dict, change: str) -> None:
        with self._changed:
            self._changed.notify_all()
        if self.bus is None:
            return
        from ..bus import Source
        self.bus.publish(self.bus.message("checklist.changed",
                                          {"step": step["id"], "package": step["package"], "change": change},
                                          Source("agent", "majordomo")))

    # -- Majordomo on the Bus -----------------------------------------------
    def as_agent(self) -> "ChecklistAgent":
        return ChecklistAgent(self)


class ChecklistAgent:
    """Majordomo's Checklist intents on the Bus: ``checklist.add|list|done|skip``.

    The requester (an agent name, or ``pkg:<id>``) is who the step is filed
    under. The Bus is in process only: agents get ``core`` trust (admin steps
    and command checks), ``pkg:<id>`` gets ``package`` trust.
    """

    name = "majordomo"

    def __init__(self, checklist: Checklist) -> None:
        self.checklist = checklist

    def handle(self, envelope: Any) -> dict:
        p = envelope.payload or {}
        who = envelope.requester or envelope.origin_agent
        package = who[4:] if who.startswith("pkg:") else who
        trusted = not who.startswith("pkg:")
        try:
            if envelope.intent == "checklist.add":
                return {"ok": True, "step": self.checklist.add(p.get("step"), package, p.get("blocks"),
                                                               trust="core" if trusted else "package")}
            if envelope.intent == "checklist.list":
                return {"ok": True, "steps": self.checklist.list(None if trusted else package)}
            if envelope.intent in ("checklist.done", "checklist.skip"):
                step = self.checklist.get(p.get("id", ""))
                if step is None or (not trusted and step["package"] != package):
                    return {"ok": False, "error": "No such step."}
                if envelope.intent == "checklist.skip":
                    return {"ok": True, "step": self.checklist.skip(step["id"], who)}
                ok, step, reason = self.checklist.done(step["id"], who)
                return {"ok": ok, "step": step, "reason": reason}
        except ChecklistError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"Majordomo does not know {envelope.intent!r}."}
