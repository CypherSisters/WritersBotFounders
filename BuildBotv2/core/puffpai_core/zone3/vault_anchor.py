"""D54: the Bridge's version, its log positions and the consent generation kept in the vault, checked at unlock.

The machine-key seals (:mod:`..anchor`) catch stray edits. A program running
as the owner that deliberately rolls PuffPAI's own files back (an old Bridge
list with its seal, the Bridge log deleted with its sealed head, an old
``consent.db``) passes them, because every file it restores was genuinely
sealed once. So the Core also keeps, through Archivist (lane L3):

    bridge.policy       {"version": int, "sha": hex}   on every Bridge policy change
    bridge.ledger       {"n": int, "h": hex}           on every outside-log line
    core.audit          {"n": int, "h": hex}           on every audit-log line
    consent.generation  {"generation": int}            on every revoke

**Lane L3's real interface** (``zone1/archivist.py`` at ``lane/l3-vault``
``936bcc5d``), used exactly: the Core's Archivist comes from
``zone1.archivist.for_state(state, bus)`` (or the one the Core already holds);
``anchor_put(name, value) -> bool`` is False while the vault is locked, and
the value is kept here as **pending** and put again at the next unlock, never
silently dropped; ``anchor_get(name) -> dict | None`` raises while locked or
when the anchor was changed outside PuffPAI, and a raise during the unlock
check **closes the Bridge with a card** (it is "can't confirm", never "no
anchor"); the check is registered with ``on_unlock(callback)``.

A change made while the vault is locked can't reach it: its value is kept as
**pending**, in memory and in ``settings/bridge/vault_pending.json`` (MAC'd with
the machine key, so it survives a restart). The **expected** state of each
anchor is its newest pending value, else the vault's copy.

At every unlock the files must say exactly the expected state: the policy's
version and sha, each log's last line (``n``, ``h``), and the consent store's
generation (and never lower than the vault's copy). A difference (old files put
back before the unlock, a log cut, an old ``consent.db``) closes the Bridge with
one card; so does an anchor Archivist can't read, and a check that fails. Then
the vault takes the values: the current ones when all agree, else the expected
ones, so every later unlock closes again until the owner opens the Bridge
herself (:meth:`VaultCheck.accept`).

At Core start (the vault usually locked) the files are compared with the
pending values on disk; a damaged pending file, or a missing one on a bot that
has run before, is a card at start (09 section 3).

**One moment for all the anchors (D151).** The Core's own writers change a file
and hand its new value here while holding that file's ``write_lock``. The check
holds every one of those locks while it reads, compares and hands the vault its
values, so a live write is never read as a rollback, and none lands in the middle
of it. Writers that do not all finish in :data:`CHECK_WAIT_S` are "can't confirm",
which closes. The locks bind only the Core's own writers, so a program outside the
Core is still read as it is. Honest limit: a write can still land between the vault
opening and these locks being held, and it replaces the vault's copy first.

Honest limit (09 section 9): a program that rolls back the files AND the
pending file together while the Core is stopped and the vault is locked is
seen at the next unlock only if it went further back than the vault's own copy.
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

logger = logging.getLogger(__name__)

#: Lane L3's module function the Core uses to reach the one Archivist of a bot folder.
ARCHIVIST_FOR_STATE = "zone1.archivist.for_state"
POLICY, LEDGER, AUDIT, GENERATION = "bridge.policy", "bridge.ledger", "core.audit", "consent.generation"
MISMATCH_LINE = ("Your Bridge settings or its record don't match what your vault remembers, so the Bridge is "
                 "closed. An old copy may have been put back. Check and open it again.")
CANT_CONFIRM_LINE = ("PuffPAI couldn't confirm your Bridge settings against your vault, so the Bridge is closed. "
                     "Check and open it again.")
#: How long the check waits in all for the writers to finish their writes and seals, in seconds (a writer can wait for
#: the vault while a PIN is checked, about a second). Longer than this: it can't confirm, and the Bridge closes (D151).
CHECK_WAIT_S = 30.0

#: The four Archivist calls this module uses, with lane L3's exact parameter names (936bcc5d).
#: tests/floor/test_l4_verifier_fixes.py checks the test double, and the real Archivist when it is here.
ARCHIVIST_CONTRACT = {
    "is_open": ["self"],
    "anchor_put": ["self", "name", "value"],
    "anchor_get": ["self", "name"],
    "on_unlock": ["self", "callback"],
}


class Archivist(Protocol):
    """The part of lane L3's Archivist the Bridge uses."""

    def is_open(self) -> bool: ...
    def anchor_put(self, name: str, value: dict) -> bool: ...            # False while locked
    def anchor_get(self, name: str) -> dict | None: ...                  # raises while locked or tampered
    def on_unlock(self, callback: Callable[["Archivist"], None]) -> Callable[[], None]: ...


def contract_problems(obj: Any) -> list[str]:
    """Names of calls whose parameters differ from :data:`ARCHIVIST_CONTRACT` ([] when they match)."""
    out = []
    for name, params in ARCHIVIST_CONTRACT.items():
        fn = getattr(type(obj), name, None) if not inspect.isclass(obj) else getattr(obj, name, None)
        if not callable(fn):
            out.append(f"{name}: missing")
            continue
        got = list(inspect.signature(fn).parameters)
        if got != params:
            out.append(f"{name}: {got} != {params}")
    return out


def find_archivist(state: Path | str | None, bus: Any = None) -> Archivist | None:
    """The bot folder's Archivist through lane L3's ``for_state``; None when lane L3 is not here yet."""
    if state is None:
        return None
    module, _, func = ARCHIVIST_FOR_STATE.rpartition(".")
    try:
        mod = importlib.import_module(f"puffpai_core.{module}")
    except ImportError:
        return None
    fn = getattr(mod, func, None)
    return fn(state, bus) if callable(fn) else None


def _policy_value(policy: Any, turned: list | None = None) -> dict:
    """With *turned*: read quietly, and note there if the policy just turned damaged (:meth:`.policy.Policy.read_quietly`)."""
    if turned is None:
        return {"version": int(policy.version), "sha": str(policy.sha())}
    version, body_sha, damaged = policy.read_quietly()
    if damaged:
        turned.append(True)
    return {"version": int(version), "sha": str(body_sha)}


def _log_value(log: Any) -> dict:
    from .chain import GENESIS, _last_line
    try:
        last = _last_line(Path(log.path))          # the file on disk, not the writer's memory
    except (OSError, ValueError):
        last = {"n": -1, "h": "unreadable"}
    return {"n": int(last["n"]), "h": str(last["h"])} if last else {"n": 0, "h": GENESIS}


def _generation_value(consents: Any) -> dict | None:
    g = consents.generation()
    return {"generation": int(g)} if g is not None else None


def _anchors(policy: Any, logs: dict[str, Any], consents: Any = None,
             turned: list | None = None) -> list[tuple[str, Any, Callable[[], dict | None]]]:
    """Each anchor: its name, the lock its writer holds across a write and its seal, and how to read its file. Always
    this order, the one the locks are taken in: a policy save can reach the audit log (a damaged policy's card can start
    safe mode), and no log writer or revoke reaches the policy."""
    out: list[tuple[str, Any, Callable[[], dict | None]]] = [(POLICY, policy.write_lock, lambda: _policy_value(policy, turned))]
    if consents is not None:
        out.append((GENERATION, consents.write_lock, lambda: _generation_value(consents)))
    out += [(name, logs[name].write_lock, lambda log=logs[name]: _log_value(log)) for name in sorted(logs)]
    return out


def current_values(policy: Any, logs: dict[str, Any], consents: Any = None) -> dict[str, dict | None]:
    """What the files say now, in the anchors' own shapes. ``consent.generation`` is None when the store
    can't vouch for its generation (edited outside PuffPAI)."""
    return {name: read() for name, _lock, read in _anchors(policy, logs, consents)}


class WriterBusy(RuntimeError):
    """The writers did not all finish in :data:`CHECK_WAIT_S`: nothing can be said. ``name``: the anchor still writing."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


@contextmanager
def _quiet(anchors: list[tuple[str, Any, Callable[[], dict | None]]]) -> Iterator[None]:
    """No writer of any anchor is between a write and its seal while this is held. All the locks, in :func:`_anchors`'
    one order, within one limit, and taken before the vault's as the writers do."""
    ends = time.monotonic() + CHECK_WAIT_S
    held: list[Any] = []
    try:
        for name, lock, _read in anchors:
            if not lock.acquire(timeout=max(0.0, ends - time.monotonic())):
                raise WriterBusy(name)
            held.append(lock)
        yield
    finally:
        for lock in reversed(held):
            lock.release()


class VaultCheck:
    """Puts anchors through Archivist, keeps what it could not put as **pending** (in memory and in a file
    MAC'd with the machine key), and compares at start and at every unlock.

    The expected state of each anchor is its newest pending value if there is one (a change made while
    the vault was locked), else the vault's own copy. At unlock the files must say exactly that; any
    difference closes the Bridge with a card. Only then does the vault take the values. After a
    mismatch the vault keeps the expected values, so every later unlock closes again until the owner
    opens the Bridge herself (:meth:`accept`), which takes the files as they are now.
    """

    def __init__(self, archivist: Archivist | None = None, *, state: Path | str | None = None, bus: Any = None,
                 anchor: Any = None) -> None:
        self.problem = ""
        if archivist is None:
            try:
                archivist = find_archivist(state, bus)
            except Exception:  # noqa: BLE001 - said plainly (status), and every unlock check then closes
                archivist, self.problem = None, "archivist_failed"
        self.archivist = archivist
        self.state = Path(state) if state is not None else None
        if anchor is None and self.state is not None:
            from .. import anchor as _anchor
            anchor = _anchor.for_state(self.state)
        self.anchor = anchor
        self.path = self.state / "settings" / "bridge" / "vault_pending.json" if self.state is not None else None
        self._lock = threading.RLock()
        self.problems: list[dict] = []
        self.mismatch = False                   # the last unlock found one; cleared when she opens the Bridge
        self._remove: Callable[[], None] | None = None
        # pending as it was on disk when the Core started (read before anything can change it)
        self.loaded: dict[str, dict] | None
        self.loaded_why = ""
        self.loaded, self.loaded_why = self._read_pending()
        self.pending: dict[str, dict] = dict(self.loaded or {})

    # -- the pending file ----------------------------------------------------------
    def _mac(self, body: dict) -> str:
        from ..anchor import canon
        return self.anchor.mac("vault_pending", canon(body))

    def _read_pending(self) -> tuple[dict[str, dict] | None, str]:
        """``(pending, "")``, ``(None, "missing")`` or ``(None, "damaged")``."""
        if self.path is None:
            return {}, ""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, "missing"
        except (OSError, ValueError):
            return None, "damaged"
        body = raw.get("pending") if isinstance(raw, dict) else None
        if not isinstance(body, dict) or self.anchor is None:
            return None, "damaged"
        from ..anchor import canon
        if not self.anchor.check("vault_pending", canon(body), raw.get("mac")):
            return None, "damaged"
        return {k: v for k, v in body.items() if isinstance(v, dict)}, ""

    def _write_pending(self) -> None:
        if self.path is None or self.anchor is None or not self.anchor.available:
            return
        body = dict(self.pending)
        text = json.dumps({"pending": body, "mac": self._mac(body)}, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)

    # -- status and registration ----------------------------------------------------
    @property
    def wired(self) -> bool:
        return self.archivist is not None

    def status(self) -> dict:
        return {"wired": self.wired, "problem": self.problem, "pending": sorted(self.pending),
                "mismatch": self.mismatch}

    def register(self, callback: Callable[[], Any]) -> None:
        """Run *callback* after every real unlock, through ``Archivist.on_unlock``."""
        if self.archivist is not None and self._remove is None:
            self._remove = self.archivist.on_unlock(lambda _a: callback())

    def unregister(self) -> None:
        if self._remove is not None:
            self._remove()
            self._remove = None

    # -- putting ------------------------------------------------------------------------
    def _put(self, name: str, value: dict) -> None:
        a = self.archivist
        ok = False
        if a is not None:
            try:
                ok = a.anchor_put(name, value) is True
            except Exception:  # noqa: BLE001 - kept pending, put again at the next unlock
                logger.warning("could not keep %s in the vault", name)
        with self._lock:
            before = dict(self.pending)
            if ok:
                self.pending.pop(name, None)
            else:
                self.pending[name] = dict(value)  # locked: kept (memory and file), never silently skipped
            if self.pending != before or self.path is not None and not self.path.exists():
                try:
                    self._write_pending()
                except OSError:
                    logger.warning("could not write the pending vault values")

    def seal_policy(self, version: int, body_sha: str) -> None:
        self._put(POLICY, {"version": int(version), "sha": str(body_sha)})

    def seal_generation(self, generation: int) -> None:
        self._put(GENERATION, {"generation": int(generation)})

    def sealer(self, name: str) -> Callable[[str, int], None]:
        """A ``ChainLog(seal=...)`` callback that keeps that log's head in the vault."""
        return lambda head, n: self._put(name, {"n": int(n), "h": str(head)})

    def accept(self, policy: Any, logs: dict[str, Any], consents: Any = None) -> None:
        """The owner opened the Bridge after a mismatch card: the files as they are now become expected. A writer
        that does not finish raises :class:`WriterBusy`: the mismatch stands and the Bridge stays closed."""
        turned: list = []
        anchors = _anchors(policy, logs, consents, turned)
        try:
            with _quiet(anchors):
                for name, _lock, read in anchors:
                    value = read()
                    if value is not None:
                        self._put(name, value)
        finally:
            if turned:
                policy.say_damaged()
        self.mismatch = False
        self.problems = []

    # -- checking --------------------------------------------------------------------
    def at_start(self, policy: Any, logs: dict[str, Any], consents: Any = None, *, has_run: bool) -> list[dict]:
        """At Core start, with the vault locked: the files against the pending values kept on disk.
        A missing pending file on a bot that has run before, a damaged one, or a mismatch is a problem."""
        if self.loaded is None:
            if self.loaded_why == "damaged" or has_run:
                return [{"what": "vault_pending", "why": f"pending_{self.loaded_why}"}]
            return []
        cur = current_values(policy, logs, consents)
        return [{"what": name, "why": "rolled_back", "kept": exp, "now": cur.get(name)}
                for name, exp in self.loaded.items() if name in cur and cur[name] != exp]

    def unlocked(self, policy: Any, logs: dict[str, Any], consents: Any = None) -> list[dict]:
        """Compare at unlock (the files against the newest expected value of each anchor); returns the
        problems ([] when all agree). Then the vault takes the values: the current ones when all agree,
        else the expected ones (so the next unlock closes again until she opens the Bridge)."""
        a = self.archivist
        if a is None:
            return [{"what": "vault", "why": self.problem}] if self.problem else []
        problems: list[dict] = []
        turned: list = []
        anchors = _anchors(policy, logs, consents, turned)
        try:
            with _quiet(anchors):                 # every file and what is expected, from one moment (D151)
                cur: dict[str, dict | None] = {}
                expected: dict[str, dict] = {}
                with self._lock:
                    pending = dict(self.pending)
                for name, _lock, read in anchors:
                    cur[name] = read()
                    try:
                        kept = a.anchor_get(name)
                    except Exception:  # noqa: BLE001 - locked or tampered: can't confirm, never "no anchor"
                        problems.append({"what": name, "why": "cant_confirm"})
                        continue
                    if kept is not None and not isinstance(kept, dict):
                        problems.append({"what": name, "why": "cant_confirm"})
                        continue
                    exp = pending.get(name, kept)
                    if exp is None:
                        continue                  # nothing expected yet: the first value is taken below
                    expected[name] = exp
                    if cur[name] is None:
                        problems.append({"what": name, "why": "store_damaged"})
                    elif cur[name] != exp:
                        problems.append({"what": name, "why": "rolled_back", "kept": exp, "now": cur[name]})
                    elif kept is not None and name in (POLICY, GENERATION):
                        field = "version" if name == POLICY else "generation"
                        if isinstance(kept.get(field), int) and cur[name][field] < kept[field]:
                            problems.append({"what": name, "why": "rolled_back", "kept": kept, "now": cur[name]})
                # The vault keeps what should be here, or the values just agreed on. Still under the locks: no newer seal
                # can be overwritten, and no change made after the compare can be taken in as agreed.
                for name, value in (expected if problems else cur).items():
                    if value is not None:
                        self._put(name, value)
        except WriterBusy as exc:                 # never a pass: it can't confirm, so the Bridge closes
            problems = [{"what": exc.name, "why": "cant_confirm"}]
        finally:
            if turned:
                policy.say_damaged()              # with the locks let go: its card can start safe mode
        self.problems = problems
        if problems:
            self.mismatch = True
        return problems


__all__ = ["ARCHIVIST_CONTRACT", "ARCHIVIST_FOR_STATE", "AUDIT", "Archivist", "CANT_CONFIRM_LINE", "GENERATION",
           "LEDGER", "MISMATCH_LINE", "POLICY", "VaultCheck", "WriterBusy", "contract_problems", "current_values",
           "find_archivist"]
