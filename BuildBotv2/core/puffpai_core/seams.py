"""The zone interfaces the Package Manager, Steward, Caretaker and ``puffpai check`` call, built by other lanes.

Each one is looked up when it is needed, by module and function name, so it
works the moment its lane is merged, with no edit here. **A missing interface
gives the floor's answer, never a quiet yes** (X26: no check means no). One
seam per thing:

| Interface | Lane | When it is missing |
|---|---|---|
| ``drawbridge_check(folder, manifest, origin=, drawbridge=)``: lane L4's ``zone3.drawbridge.Drawbridge.check_package`` (the Core's own Drawbridge, with its audit log, when it is given) | L4 | nothing is added |
| ``drawbridge_bound(folder, verdict)``: lane L4's ``zone3.drawbridge.verify_tree`` (allow, warn) or its ``tree_sha`` (hold, after her Yes) | L4 | False: nothing is installed |
| ``check_pin(state, pin, bus)``: ``zone1.archivist.for_state(state, bus).verify_pin(pin)`` (else module ``verify_pin(pin)``). The one PIN check: the consent store's floor-level Yes (D29) and the Desktop's ``pin_check`` (run as administrator) both call it | L3 | a floor-level Yes card can't be answered Yes (D29) |
| ``zone1.archivist.for_state(state, bus)`` then ``.inject(package_id, key_names) -> {name: value}`` (else module ``lease_for_start``) | L3 | the package starts with no keys |
| ``zone1.archivist.for_state(state, bus)`` then ``.end_holder(PACKAGE_HOLDER + id) -> int`` (else module ``end_lease(id)``) | L3 | nothing to end (logged) |
| ``zone2.brain.sample_ask(tool, ask) -> bool`` | L2 | ``puffpai check`` item 12 says "skipped: brain not built yet" |
| ``zone3.docker_wall.override(manifest, compose_path, package_dir=) -> path`` | L4 | a ``docker_compose`` package does not start (D30) |

The Archivist is always the one for the Core's own bot folder (``for_state(state, bus)``), not
whatever ``state_dir()`` says, so a Core started on another folder never asks the wrong vault.
The module-level functions are used only when ``for_state`` is missing.

Drawbridge's verdict is lane L4's dict: ``{"verdict": "allow" | "warn" | "hold" | "block",
"reasons", "rows", "tree_sha", "tools", "line", ...}``. ``allow`` and ``warn`` go on to the
owner's Yes card (a warn is said on it). ``hold`` (a file it can't check, a tunnel, a community raw reader)
goes on only to her Yes for exactly that: the card shows what is held, and her Yes on that card is
the Yes (09 section 6: "her Yes, never a pass"). ``block``, any other word, an exception, or a
``tree_sha`` that is not the staged folder's (L4's own hash of every file) is a no.
``override`` (lane L4) reads the app's own compose file, refuses what can get around
the Bridge, and writes ``<package>\\.buildbot\\compose.puffpai.yml`` itself; it returns
that path (a dict is still accepted, and Steward writes it).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

DRAWBRIDGE = "zone3.drawbridge"            # lane L4: Drawbridge, verify_tree, tree_sha
VERIFY_PIN = "zone1.archivist.verify_pin"
LEASE_FOR_START = "zone1.archivist.lease_for_start"
END_LEASE = "zone1.archivist.end_lease"      # module fallback; Steward uses end_holder()
ARCHIVIST_FOR_STATE = "zone1.archivist.for_state"
SAMPLE_ASK = "zone2.brain.sample_ask"
DOCKER_OVERRIDE = "zone3.docker_wall.override"
DOCKER_GATE_PROBLEM = "zone3.docker_wall.gate_problem"   # plain Linux Docker: can the gate reach the proxy?


def find(name: str) -> Callable[..., Any] | None:
    """The function *name* (``zone<N>.<module>.<function>``), or None when it is not built."""
    module, _, func = name.rpartition(".")
    try:
        mod = importlib.import_module(f"puffpai_core.{module}")
    except ImportError as exc:
        logger.info("%s is not here (%s)", name, exc)
        return None
    fn = getattr(mod, func, None)
    return fn if callable(fn) else None


def _module(name: str) -> Any | None:
    try:
        return importlib.import_module(f"puffpai_core.{name}")
    except ImportError as exc:
        logger.info("%s is not here (%s)", name, exc)
        return None


# -- Drawbridge (lane L4) -----------------------------------------------------------------------
def drawbridge_here() -> bool:
    """Lane L4's Drawbridge can run here (the Core's self-test X4)."""
    mod = _module(DRAWBRIDGE)
    return (mod is not None and callable(getattr(mod, "Drawbridge", None))
            and callable(getattr(mod, "verify_tree", None)))


def drawbridge_check(folder, manifest: dict, *, origin: str = "local", drawbridge: Any = None) -> dict | None:
    """Lane L4's install check on exactly this folder, or None when Drawbridge is not here.

    *drawbridge* is the Core's own (``zone3.service.Zone3.drawbridge``, with the audit log); without
    one a fresh ``Drawbridge()`` runs the same fixed rules. Errors are the caller's (a crash is a no)."""
    mod = _module(DRAWBRIDGE)
    if mod is None or not callable(getattr(mod, "Drawbridge", None)):
        return None
    db = drawbridge if drawbridge is not None else mod.Drawbridge()
    return db.check_package(folder, manifest, origin=origin)


def drawbridge_bound(folder, verdict: Any, drawbridge: Any = None) -> bool:
    """The folder is byte for byte what Drawbridge checked (X24), and the verdict lets it go on.

    ``allow`` / ``warn``: lane L4's ``verify_tree``, through *drawbridge*'s own ``verify`` when given (it
    knows the owner's setting for files it can't read now, D95). ``hold``: the same tree hash (the hold is
    what her Yes card showed), under the same setting rule. Anything else, or Drawbridge not here: False."""
    mod = _module(DRAWBRIDGE)
    if mod is None or not isinstance(verdict, dict) or not verdict.get("tree_sha"):
        return False
    word = verdict.get("verdict")
    own = getattr(drawbridge, "verify", None) if drawbridge is not None else None
    mode = getattr(drawbridge, "mode", None) if drawbridge is not None else None
    if word in ("allow", "warn"):
        verify = own if callable(own) else getattr(mod, "verify_tree", None)
        return callable(verify) and verify(folder, verdict) is True
    if word == "hold":
        sha = getattr(mod, "tree_sha", None)
        bound = getattr(mod, "mode_bound", None)
        if callable(bound) and not bound(verdict, mode() if callable(mode) else None):
            return False
        return callable(sha) and sha(folder) == verdict["tree_sha"]
    return False


# -- Archivist (lane L3) ------------------------------------------------------------------------
def archivist(state, bus: Any = None) -> Any | None:
    """Lane L3's one Archivist for this bot folder, or None when it is not built."""
    for_state = find(ARCHIVIST_FOR_STATE)
    return None if for_state is None else for_state(state, bus)


def _holder(package_id: str) -> str:
    try:
        from .zone1 import archivist as mod
        prefix = getattr(mod, "PACKAGE_HOLDER", "pkg:")
    except ImportError:
        prefix = "pkg:"
    return f"{prefix}{package_id}"


def verify_pin(state, pin: str, bus: Any = None) -> bool | None:
    """The PIN check for a floor-level Yes (D29): True or False, or None when there is no PIN check at all.
    Errors are the caller's (a crashing check is a no)."""
    a = archivist(state, bus)
    if a is not None:
        return a.verify_pin(pin) is True
    fn = find(VERIFY_PIN)
    return None if fn is None else fn(pin) is True


PIN_WRONG = "That PIN is not right."
PIN_NOT_SET_UP = "Set up your vault and its PIN first; until then this can't be answered Yes."
PIN_NOT_HERE = "The PIN check is not here yet, so this can't be answered Yes."


def check_pin(state, pin: Any, bus: Any = None) -> tuple[int, str, str] | None:
    """THE PIN check (D29): None when the PIN is right, else ``(status, plain reason, code)``.

    Lane L4's consent store (a floor-level Yes) and lane L5's ``pin_check`` (run as administrator)
    both call this, so there is one PIN check. A crashing check is a no; no vault yet is 503."""
    text = pin if isinstance(pin, str) else ""
    try:
        ok = verify_pin(state, text, bus)
    except Exception as exc:  # noqa: BLE001 - a crashing check is a no
        if type(exc).__name__ == "NotSetUp":
            return 503, PIN_NOT_SET_UP, "vault_not_set_up"
        logger.warning("the PIN check failed: %s", type(exc).__name__)
        ok = False
    if ok is None:
        return 503, PIN_NOT_HERE, "no_pin_check"
    return None if ok is True else (403, PIN_WRONG, "step_up_failed")


def pin_check(state, bus: Any = None):
    """Lane L5's ``app.extensions["puffpai.pin_check"]`` form of :func:`check_pin`: ``(status, reason)`` or None."""
    def check(pin: str) -> tuple[int, str] | None:
        refused = check_pin(state, pin, bus)
        return None if refused is None else (refused[0], refused[1])
    return check


def lease_for_start(state, package_id: str, key_names: list[str], bus: Any = None) -> dict | None:
    """The keys a package gets at start (ticked keys only, D27, D41), or None when the vault is not built."""
    a = archivist(state, bus)
    if a is not None:
        return a.inject(package_id, list(key_names))
    fn = find(LEASE_FOR_START)
    return None if fn is None else fn(package_id, list(key_names))


def end_holder(state, package_id: str, bus: Any = None) -> int | None:
    """End every key lease the package holds (D41: its lease ends when it stops).

    Lane L3's Archivist lends package keys to the borrower ``pkg:<id>`` (``PACKAGE_HOLDER``) and ends
    them with ``Archivist.end_holder(borrower)``. Returns how many ended, or None when Archivist is not
    here (then there are no leases to end; the caller logs it). Errors are the caller's to log."""
    a = archivist(state, bus)
    if a is not None:
        return int(a.end_holder(_holder(package_id)) or 0)
    fn = find(END_LEASE)
    if fn is None:
        logger.info("key leases of %s not ended: Archivist is not here", package_id)
        return None
    return int(fn(package_id) or 0)


__all__ = ["ARCHIVIST_FOR_STATE", "DOCKER_OVERRIDE", "DRAWBRIDGE", "END_LEASE", "LEASE_FOR_START", "SAMPLE_ASK",
           "VERIFY_PIN", "archivist", "check_pin", "drawbridge_bound", "drawbridge_check", "drawbridge_here",
           "end_holder", "find", "lease_for_start", "pin_check", "verify_pin"]
