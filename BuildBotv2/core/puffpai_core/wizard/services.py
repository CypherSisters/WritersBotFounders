"""What the wizard engine needs from the other lanes, as interfaces.

The engine never imports another lane's code by name. It gets a
:class:`Services` with five parts; a part that is ``None`` is "not built
yet", and the step that needs it says so in one plain line, never a pretend
success. :func:`default_services` looks each part up on the running Core
(``api.app.Core``) by attribute, so a lane plugs in by setting it there:

- ``core.vault``     lane L3, :class:`Vault`
- ``core.models``    lane L2, :class:`Models` (or the module ``puffpai_core.zone2.models``)
- ``core.bridge``    lane L4, :class:`Bridge`
- ``core.toolsmith`` lane L6, :class:`Toolsmith`
- ``core.packages``  the Package Manager, as :class:`CorePackages` (store apps ticked on the plan)
- ``brain``          the local brain's small JSON ask, ``(system, user) -> parsed JSON or None``, think
                     off (the name and look steps); set by the Core API's wizard group

Tests use doubles of the first four.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Vault(Protocol):
    """Zone 1, as the wizard uses it (lane L3). No method returns a stored secret."""

    def status(self) -> str: ...                     # "missing" | "locked" | "open"
    # Seal a new vault ("here" or "folder"); returns the recovery code, once. ValueError = plain reason.
    def create(self, where: str, folder: str | None, pin: str) -> str: ...
    def open_existing(self, folder: str, pin: str) -> None: ...
    # A finished bot's vault step run again, vault already open: checks the PIN, opens nothing. ValueError = plain reason.
    def confirm_open(self, folder: str, pin: str) -> None: ...
    def test_recovery(self, code: str) -> bool: ...  # the code typed back matches. ValueError = plain reason.
    # Setup's typed-back step only: a new code, once, the old one stops working. Needs the PIN typed again. A locked
    # vault is refused unless *may_open*: then that PIN opens it first (D106). ValueError = plain reason, and the
    # old code still works.
    def rotate_recovery(self, pin: str, may_open: bool = False) -> str: ...
    def new_code_problem(self) -> str: ...           # why a new code can't be made even with the PIN, or ""
    def put_secret(self, name: str, value: str) -> None: ...   # vault open
    def deposit(self, name: str, value: str) -> None: ...      # vault inbox: sealed, no PIN, filed later


@runtime_checkable
class Models(Protocol):
    """The model slot, as the wizard uses it (lane L2)."""

    # L2's recommender (zone2.models.fit): {"options": [{"id", "label", "disk_gb", "installed", "fit",
    # "recommended", "reason", ...}], "recommended", "already_here", "too_big", "message"}, strongest first.
    def fit(self, census: dict) -> dict: ...
    def menu(self, census: dict) -> list: ...         # fit(census)["options"]
    # {"state": "not_downloaded|loading|ready|timed_out|error", "done_gb", "size_gb", "reason"} (03 1.4).
    def state(self, name: str) -> dict: ...
    def pull(self, name: str) -> None: ...           # start the download in the background


@runtime_checkable
class Bridge(Protocol):
    """Zone 3 posture, as the wizard uses it (lane L4)."""

    def set_posture(self, posture: str) -> None: ...  # "search" (Toolsmith may search) | "closed"


@runtime_checkable
class Toolsmith(Protocol):
    """Toolsmith, as the wizard uses it (lane L6)."""

    # Installed zone3.toolsmith-model providers: [{"id", "company", "models": [{"id", "name", "prices": {"in", "out"}}], "default_model", "consent", "cost_cap_usd"}].
    def providers(self) -> list[dict]: ...
    # {"build": "<id>", "jobs": [{"name", "ask"}], "brain": "<state, if the brain was asked and failed>",
    #  "left_out": ["<what she asked for that is not a job>"], "left_out_line": "<one plain line>"} (D84).
    def interpret(self, goal: str, use_brain: bool) -> dict: ...
    # {"summary": "<one sentence>", "items": ["<plain line>", ...]}.
    def plan(self, build: str, project: str | None = None) -> dict: ...
    # The ONE Yes; {"ok": bool, "reason"}. *use*: the job numbers of the plan card's offers she ticked (lane
    # I4b); a Toolsmith with ``offers(build) -> [{job, job_name, package, name, tool, line}]`` gets them.
    def yes(self, build: str, use: list | None = None) -> dict: ...
    def start(self, build: str) -> dict: ...         # {"ok": bool, "reason"}
    def results(self, build: str) -> list[dict]: ... # [{"name", "state", "reason", "library"?}] (D123)


@runtime_checkable
class Packages(Protocol):
    """The Package Manager, as the build step uses it to add the PuffPAI store apps she ticked (lane I10c)."""

    # Raise its Yes card: {"consent_id", "id", "title", "lines", "floor"}, or {"id", "problems"}.
    def stage(self, id: str) -> dict: ...
    def answer(self, consent_id: str, yes: bool) -> str: ...   # "" when taken, else the plain reason
    def wait(self, consent_id: str) -> dict: ...     # {"status": "installed|declined|failed", "problems"}


class CorePackages:
    """:class:`Packages` on a running Core: the Core's own packages folder, the Package Manager and the Yes
    cards (the same add, card and answer as ``puffpai add``)."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self.cards: dict[str, dict] = {}

    def stage(self, id: str) -> dict:
        from ..consent import card_view
        from ..packages.manager import AddError
        try:
            staged = self.core.packages.add(id)
        except AddError as exc:
            return {"id": id, "problems": list(exc.problems)}
        self.cards[staged["consent_id"]] = staged["card"]
        seen = card_view(staged["card"], staged["floor"])
        return {"consent_id": staged["consent_id"], "id": staged.get("id") or id, "title": seen["title"],
                "lines": list(seen["lines"]), "floor": bool(staged["floor"])}

    def answer(self, consent_id: str, yes: bool) -> str:
        from ..consent import ConsentError, card_sha
        card = self.cards.pop(consent_id, None)
        try:
            if yes and card is not None:
                self.core.consent.answer(consent_id, "yes", "tap", by="owner", card_sha=card_sha(card))
            else:
                self.core.consent.answer(consent_id, "no", by="owner")
        except ConsentError as exc:
            return exc.message
        return ""

    def wait(self, consent_id: str) -> dict:
        return self.core.packages.wait(consent_id, timeout_s=1800)


@dataclass
class Services:
    vault: Any = None
    models: Any = None
    bridge: Any = None
    toolsmith: Any = None
    packages: Any = None
    brain: Any = None


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def default_services(core: Any) -> Services:
    models = getattr(core, "models", None)
    if models is None:
        mod = _module("puffpai_core.zone2.models")
        if mod is not None and callable(getattr(mod, "fit", None)) and callable(getattr(mod, "state", None)):
            models = mod
    packages = CorePackages(core) if getattr(core, "packages", None) is not None else None
    return Services(vault=getattr(core, "vault", None), models=models,
                    bridge=getattr(core, "bridge", None), toolsmith=getattr(core, "toolsmith", None),
                    packages=packages)
