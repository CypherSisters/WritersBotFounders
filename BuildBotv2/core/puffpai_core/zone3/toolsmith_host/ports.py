# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/pipeline.py (rewritten-from)
"""Every call the Core's side of Toolsmith makes to another lane, in one place (the one adapter).

This runs in the Core process. Toolsmith's own process never imports it: it
reaches these through its Toolsmith-token routes (``toolsmith_host.service``).
Each interface is looked up when it is needed, so it works the moment its
lane is merged. **A missing interface gives the floor's answer, never a quiet
yes** (X26): nothing installs, nothing leaves, nothing runs unchecked.

L4's interfaces are matched to lane/l4-bridge at ``a0764ba2`` exactly:

| Port (method here) | Interface it calls | When it is missing |
|---|---|---|
| ``zone_check`` | ``zone3.toolsmith_zone.check(target, folder=, kind=, write=, cwd=, logs=, scratch=, own_pids=, owner_step=) -> Verdict(verdict, line, outside)`` | ``deny`` |
| ``drawbridge_check`` | ``zone3.service.current().drawbridge.check_package(path, manifest, origin=) -> {verdict: allow\\|warn\\|hold\\|block, reasons, tree_sha, line}`` | ``block`` |
| ``verify_tree`` | ``zone3.drawbridge.verify_tree(path, verdict) -> bool`` | False |
| ``hide_ai_lines`` | ``zone3.text_rules.hide_ai_lines(text) -> (text, n)`` | NotBuilt |
| ``bridge_decision`` / ``bridge_allowed`` | ``zone3.service.current().bridge.allowed(subject, host, labels, phase=) -> Decision(verdict)`` | ``deny`` |
| ``proxy_env`` | ``zone3.service.current().credentials.issue(name, subject=, phase=)`` + ``zone3.proxy.proxy_env(url)`` | NotBuilt (nothing runs that could go around the Bridge) |
| ``bridge_refused`` | ``zone3.service.current().bridge.ledger.refused_since(subject, after)`` (D70) | none (no line) |
| ``docker_override`` | ``zone3.docker_wall.override(manifest, compose_path) -> Path`` (raises ``WallRefused``) | NotBuilt |
| ``consent_request`` / ``consent_record`` | ``zone3.service.current().consents``: ``ConsentStore.request(kind, target, covers, floor=) -> card`` / ``.get(id) -> record or None`` | NotBuilt |

Zone 3 is the running Core's own (``zone3.service.for_app(app)``, started once at Core
start in ``api.app.serve``, or on first use by a Zone 3 route), passed in as ``zone``;
``service.current()`` is only the fallback. Not built (no L4 code) and not running (no Zone 3 started) are two
different plain lines, and both are the floor's answer.

L3's interfaces are matched to lane/l3-vault ``936bcc5d`` exactly:

| Port | Interface | When it is missing |
|---|---|---|
| ``screen_outbound`` | ``zone1.classify.classify(text, path="outbound") -> Classified``; every span it finds (all labels say ``cloud: strip``) is taken out, keys as ``[key]`` | NotBuilt (nothing is sent) |
| ``lend_key`` / ``key_value`` / ``end_lease`` | ``zone1.archivist.for_state(state, bus)``: ``Archivist.lend(key_name, borrower, purpose, *, hosts=, job=)``, ``.use(handle, *, borrower, purpose, host=) -> bytes``, ``.end(handle, why)``; ``vault_open`` / ``lease_live``: ``.is_open()``, ``.live(handle)`` (lane I19a, read only) | KeyUnavailable ``not_built`` |

L1's interfaces are the Core's own objects (``api.app.Core``), matched to main ``3881f0d4``:

| Port | Interface | When it is missing |
|---|---|---|
| ``packages_add`` / ``packages_wait`` | ``PackageManager.add(source, link=False) -> {consent_id, id, card, card_sha, floor}`` (fetch into staging, validate, Drawbridge bound to the staged tree hash, ONE Yes card); after her Yes on that card the Package Manager installs on its own; ``PackageManager.wait(cid, timeout_s) -> {status: installed, declined, failed, pending}`` | NotBuilt (nothing installs) |
| ``steward_start`` | ``Steward.start(id) -> row`` | NotBuilt |
| ``hook_guard`` | L4 ``zone3.hook_guard.HookGuard(folder, stop_try=, safe_mode=, on_card=)`` around every command | NotBuilt (nothing runs) |
| ``safe_mode`` | Caretaker's ``safe_mode(reason)`` (the one Core call) | the one fallback: Caretaker's shared ``enter_safe_mode`` over ``safe_mode_steps`` here (same steps, same card) |

Other lanes:

| Port | Interface | Lane | Missing |
|---|---|---|---|
| ``local_turn`` / ``brain_json`` / ``brain_answer`` | ``zone2.brain.toolsmith_turn`` / ``.ask_json`` / ``.answer`` | L2 | NotBuilt / None |
| ``read_page`` / ``read_app`` | ``zone2.apps.read_page(url)`` / ``.read_app(find, start, args)`` | L2 | NotBuilt |
| ``checklist_add`` / ``checklist_state`` | ``Checklist.add(step, package, blocks)`` / ``.get(id)`` | L5 | NotBuilt |

``http_send`` and ``http_get`` (the Core's own outside calls for Toolsmith)
live here too, because each must get the Bridge's yes first. So does
``public_get``, one hop of a cloud build's read of a public page (D121).
"""

from __future__ import annotations

import contextlib
import functools
import http.client
import importlib
import logging
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..toolsmith.errors import NotBuilt, Refused

logger = logging.getLogger(__name__)

#: A generic User-Agent: no name, no address, no account (never an identifier).
USER_AGENT = "PuffPAI-BuildBot/2 (+https://github.com)"
#: The knowledge fetches' User-Agent (lane I11): Wikimedia's policy wants a way to reach the operator, so it
#: names the project's organisation page. Not a person, no email, nothing about this computer.
KNOWLEDGE_USER_AGENT = "PuffPAI-BuildBot/2 (+https://github.com/CypherSisters)"
VERDICTS = ("allow", "deny", "ask")
#: What a public read asks for (D121): the only kinds it keeps.
PUBLIC_ACCEPT = "text/html,application/xhtml+xml,text/markdown,text/plain,application/json"


class Redirected(Refused):
    """The other end answered with a redirect; nothing more was sent (``status``, ``host``)."""

    def __init__(self, host: str, status: int) -> None:
        super().__init__(f"{host} answered with a redirect, so nothing more was sent.")
        self.host, self.status = host, status
        self.location = ""                         # where it points: only for a caller that checks it again first


class NotPublic(Refused):
    """A name that looked up to a private address, or to nothing. Nothing was sent. ``code``: the outside log's own."""

    def __init__(self, line: str, code: str) -> None:
        super().__init__(line)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The Core never follows a redirect: a 3xx comes back as the answer itself. Following one would
    reach a host the Bridge never decided on, carrying the request's headers (a key) with it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib's hook
        return None


class _PinnedConnection(http.client.HTTPSConnection):
    """Dials only the addresses that passed the check, so a second lookup can't swap one. SNI and the certificate check
    stay on the host name, since ``self.host`` is still the name."""

    def __init__(self, host: str, *, addresses: tuple[str, ...], guard: Callable[[], None] | None = None, **kw: Any) -> None:
        super().__init__(host, **kw)
        self._addresses = addresses
        self._guard = guard
        self._create_connection = self._dial

    def connect(self) -> None:
        """*guard* right before dialling and again before any request goes: a send the caller has given up on, or
        whose Yes has gone, stops here with its key unsent."""
        if self._guard is not None:
            self._guard()
        super().connect()
        if self._guard is not None:
            try:
                self._guard()
            except BaseException:
                self.close()
                raise

    def _dial(self, address: tuple, timeout: Any = None, source_address: Any = None) -> socket.socket:
        failed: OSError | None = None
        for addr in self._addresses:
            try:
                return socket.create_connection((addr, address[1]), timeout, source_address)
            except OSError as exc:
                failed = exc
        raise failed or OSError("no address")


class _PinnedHTTPS(urllib.request.HTTPSHandler):
    def __init__(self, addresses: tuple[str, ...], guard: Callable[[], None] | None = None) -> None:
        super().__init__()
        self._pin = functools.partial(_PinnedConnection, addresses=addresses, guard=guard)
        self._tls = ssl.create_default_context()         # CERT_REQUIRED and check_hostname

    def https_open(self, req: Any) -> Any:
        return self.do_open(self._pin, req, context=self._tls)


class TooSlow(TimeoutError):
    """A send ran past its deadline (an ``OSError``): a slow trickle never holds a plan. Plain words, no reply text."""

    def __init__(self, host: str, seconds: float) -> None:
        super().__init__(f"{host} took longer than {seconds:g} seconds, so the rest was not read.")


def _read_within(reply: Any, max_bytes: int, deadline: float, host: str, seconds: float) -> bytes:
    """Up to *max_bytes* of *reply*, in chunks, until the wall-clock *deadline*. One chunk is one read off the socket
    (``read1``): a full ``read(n)`` would sit through a slow trickle, each byte renewing the socket's own timeout."""
    read = getattr(reply, "read1", None) or reply.read
    out = bytearray()
    while len(out) < max_bytes:
        if time.monotonic() >= deadline:
            raise TooSlow(host, seconds)
        chunk = read(min(65536, max_bytes - len(out)))
        if not chunk:
            break
        out += chunk
    return bytes(out)


def _within(seconds: float, host: str, fn: Callable[[], Any]) -> Any:
    """*fn*'s answer, or :class:`TooSlow` at *seconds*: headers can trickle too, where no chunk loop reaches. *fn* is
    left to end on its own socket timeout; its answer is never used. For a GET only: nothing is lost by leaving it."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["got"] = fn()
        except BaseException as exc:  # noqa: BLE001 - raised again in the caller
            box["failed"] = exc
    worker = threading.Thread(target=run, name="core-get", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TooSlow(host, seconds)
    if "failed" in box:
        raise box["failed"]
    return box["got"]


#: One Test at a time: each stands on the one subject, and its clean-up drops that subject's grants.
_ONE_SEND = threading.Lock()


class KeyUnavailable(RuntimeError):
    """A cloud key can't be lent. ``reason``: missing | locked | ended | not_built | refused."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ZoneAnswer:
    """L4's ``Verdict`` shape: ``verdict`` allow | deny | ask, ``line`` (a deny's plain words,
    never a real path), ``outside`` (what waits for her Yes)."""
    verdict: str
    line: str = ""
    outside: tuple = ()

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


#: The "You" answers the knowledge topics must never carry (lane I12's ``puffpai_core.you``).
YOU_KEYS = ("name", "call_me")


def you_names(state: Path | str) -> list[str]:
    """The owner's own name and what she is called, from lane I12's "You" settings (``puffpai_core.you.load``:
    the values are in the vault, its settings file keeps only a ref); [] when she gave none.

    Fail closed: when You data is kept but can't be read (the vault is locked, or the item is not filed yet),
    this raises, so the caller keeps no knowledge topic at all (lane I14)."""
    from ... import you
    got = you.load(Path(state))                   # you.YouLocked while the vault is locked and something is kept
    if you._ref(Path(state)) and not any(str(v or "").strip() for v in got.values()):
        raise you.YouLocked()                     # kept, but nothing could be read: the names can't be checked
    return [str(got[k]).strip() for k in YOU_KEYS if isinstance(got.get(k), str) and got[k].strip()]


def strip_hidden(text: str) -> str:
    """*text* without hidden characters (control, format, private use, blanks, selectors, tags: the one shared
    list, ``zone3.text_rules.hidden_char``, lane I14); line breaks and tabs stay."""
    from ..text_rules import hidden_char
    return "".join(ch for ch in text if ch != "\r" and not hidden_char(ch, lines=True))


def find(name: str) -> Callable[..., Any] | None:
    """``puffpai_core.<module>.<function>``, or None when that lane has not landed."""
    module, _, func = name.rpartition(".")
    try:
        mod = importlib.import_module(f"puffpai_core.{module}")
    except ImportError:
        return None
    fn = getattr(mod, func, None)
    return fn if callable(fn) else None


def _need(name: str, what: str, lane: str) -> Callable[..., Any]:
    fn = find(name)
    if fn is None:
        raise NotBuilt(what, lane)
    return fn


def zone3() -> Any:
    """The process's current Zone 3 (L4 ``service.current()``), or None. A fallback only."""
    current = find("zone3.service.current")
    if current is None:
        return None
    try:
        return current()
    except Exception:  # noqa: BLE001 - not running is not there
        return None


def _vault_reason(exc: BaseException) -> str:
    name = type(exc).__name__
    if name == "Locked":
        return "locked"
    if name in ("NotFound", "NotSetUp") or isinstance(exc, (KeyError, LookupError)):
        return "missing"
    if name == "LeaseCapped":
        return "cap"                      # its 3 hour limit (X22): said plainly, never raised silently
    if name == "LeaseEnded":
        return "ended"
    return "refused"


class Ports:
    """The production adapter. Tests pass a fake with the same methods."""

    def __init__(self, state: Path | str, registry: Any = None, checklist: Any = None,
                 consent: Any = None, bus: Any = None, zone: Any = None, packages: Any = None,
                 steward: Any = None, caretaker: Any = None) -> None:
        self.state = Path(state)
        self.registry = registry
        self.checklist = checklist
        self.consent = consent
        self.bus = bus
        self.packages = packages              # L1 PackageManager (the Core's one install path)
        self.steward = steward                # L1 Steward
        self.caretaker = caretaker            # L1 Caretaker
        self.zone = zone                      # the Core's own Zone 3 (or a callable that gives it)
        self.toolsmith: Any = None            # the ToolsmithHost these ports serve (it sets itself here)

    # -- Zone 3: the running Core's own ----------------------------------------

    def zone3(self) -> Any:
        """The Core's own Zone 3 when one was given (None = not running); else the process's current one."""
        if self.zone is None:
            return zone3()
        try:
            return self.zone() if callable(self.zone) else self.zone
        except Exception:  # noqa: BLE001 - no answer: not running
            return None

    @staticmethod
    def zone3_line(what: str) -> str:
        """Why a Zone 3 part can't answer: its lane has not landed, or it is not running."""
        if find("zone3.service.for_app") is None:
            return f"{what} is not built yet (lane L4)"
        return f"{what} is not running in this Core"

    # -- L1: the Package Manager ----------------------------------------------

    def packages_add(self, folder: Path, plan: dict | None = None, adopted: str | None = None,
                     official_only: bool = False) -> dict:
        """Hand a folder to the one install path: ``{consent_id, id, card, card_sha, floor}``.

        The Package Manager copies it into staging (no ``.venv``, ``node_modules`` or ``.git``),
        checks it with Drawbridge bound to that tree hash, and raises its ONE Yes card. Toolsmith
        never answers that card. With *plan* (the build's plan Yes, ``packages.plan_cover``) the
        Package Manager checks the app against the plan itself: inside it, the plan's Yes covers it
        (``covered_by``); outside it, she answers a card for the difference. Refused: ``Refused``.

        *adopted* is the address on the plan the try came from (the Core's own record, never read from a
        file in the folder): the Registry keeps ``origin: adopted:<url>`` (02), and the Package Manager
        reads only the ``.buildbot/puffpai.json`` the Core wrote.

        *official_only* (a plan's ``store:<id>``, D96): only the official store, as it reads now, can supply it."""
        if self.packages is None or not hasattr(self.packages, "add"):
            raise NotBuilt("The Package Manager", "L1")
        kw = {}
        if official_only:
            kw["official_only"] = True
        if adopted:
            kw["adopted"] = str(adopted)
        try:
            if plan is not None:
                kw["plan"] = plan                       # the build's plan Yes (03 3.3, packages.plan_cover)
            got = self.packages.add(str(folder), **kw)
        except ValueError as exc:                      # AddError: plain problems
            problems = getattr(exc, "problems", None) or [str(exc)]
            raise Refused(" ".join(str(x) for x in problems)) from None
        if not isinstance(got, dict) or not got.get("consent_id"):
            raise Refused("The Package Manager gave no card, so nothing was installed.")
        return got

    def store_row(self, pkg_id: str) -> dict | None:
        """The official store's row for *pkg_id* as it reads now (her Yes on the plan asked for this add), or None
        when it no longer lists it (D96). Raises when there is nothing to tell by."""
        store = getattr(self.packages, "store", None)
        if store is None:
            raise NotBuilt("The Package Manager", "L1")
        return store.official_row(pkg_id)

    def packages_wait(self, consent_id: str, timeout_s: float = 0.0) -> dict:
        """The install's state after her answer: ``{status: installed | declined | failed | pending, ...}``."""
        if self.packages is None or not hasattr(self.packages, "wait"):
            raise NotBuilt("The Package Manager", "L1")
        got = self.packages.wait(consent_id, timeout_s)
        return got if isinstance(got, dict) else {"status": "pending"}

    def steward_start(self, pkg_id: str) -> dict:
        """Start an installed package from its manifest (Steward). Refused: its plain reason."""
        if self.steward is None or not hasattr(self.steward, "start"):
            raise NotBuilt("Steward", "L1")
        try:
            got = self.steward.start(pkg_id)
        except RuntimeError as exc:                    # StewardError
            raise Refused(str(exc)) from None
        return got if isinstance(got, dict) else {}

    def hook_guard(self, folder: Path | str, *, stop_try: Any, safe_mode: Any, on_card: Any) -> Any:
        """L4's Python start-up check around one command (X23): ``with guard: run(...)``, then ``guard.result``."""
        guard = find("zone3.hook_guard.HookGuard")
        if guard is None:
            raise NotBuilt("The Python start-up check", "L4")
        return guard(str(folder), stop_try=stop_try, safe_mode=safe_mode, on_card=on_card)

    def safe_mode(self, reason: str) -> list[str]:
        """Safe mode (01 "Core start"): Caretaker's one call, ``caretaker.safe_mode(reason)`` (it closes
        the Bridge, locks the vault, pauses Toolsmith and raises the card). What was done, in plain words.

        **The only other path** (a last resort, so it still fails safe): with no Caretaker, or when its
        call raises, this runs Caretaker's own shared effect here, ``enter_safe_mode`` over
        ``safe_mode_steps`` (the same three steps and the same ``safe_mode`` card, given to Caretaker's
        card listeners, so the same notice). Its line does not say "nothing new is added": without a
        working Caretaker the Package Manager can't be told."""
        c = self.caretaker
        if c is not None and callable(getattr(c, "safe_mode", None)):
            try:
                got = c.safe_mode(reason)
                return [str(x) for x in (got or {}).get("done") or []]
            except Exception:  # noqa: BLE001 - the fallback below still closes, locks and pauses
                logger.exception("caretaker.safe_mode failed; the fallback runs")
        from ...caretaker.caretaker import enter_safe_mode, plain_reason, safe_mode_steps
        why = plain_reason(reason)
        steps = safe_mode_steps(zone3=self.zone3, state=self.state, bus=self.bus,
                                toolsmith=lambda: self.toolsmith, reason=lambda: why)
        listeners = list(getattr(c, "on_card", None) or []) if c is not None else []
        cards = getattr(c, "cards", None) if c is not None else None
        done, _card = enter_safe_mode(why, steps, listeners, cards if isinstance(cards, list) else None,
                                      adds_nothing=False)
        return done

    # -- consent cards (L4 ConsentStore) --------------------------------------

    def _consents(self) -> Any:
        if self.consent is not None:
            return self.consent
        z = self.zone3()
        store = getattr(z, "consents", None) if z is not None else None
        if store is None or not hasattr(store, "request") or not hasattr(store, "get"):
            raise NotBuilt("The consent cards", "L4")
        return store

    def consent_request(self, kind: str, target: str, covers: dict, floor: bool = False,
                        bound: dict | None = None) -> str:
        """Raise one Core card. Returns its consent id.

        The Core's one store (L4 head, ``consent/store.py``): ``ask(kind, target, covers, floor=, bound=)``;
        L4 a0764ba2's ``zone3/consent.py``: ``request(kind, target, covers, floor=, bound=)``."""
        store = self._consents()
        ask = getattr(store, "ask", None)
        if callable(ask):
            card = ask(kind, target, covers, floor=floor, bound=bound)
        else:
            card = store.request(kind, target, covers, floor=floor, bound=bound)
        cid = card.get("consent_id") if isinstance(card, dict) else None
        if not cid:
            raise NotBuilt("The consent cards", "L4")
        return str(cid)

    def consent_record(self, cid: str) -> dict | None:
        """The live Yes record, or None. A store that also returns cards (pending, declined, void,
        damaged...) gives a Yes only for status ``granted``."""
        got = self._consents().get(cid)
        if not isinstance(got, dict):
            return None
        if "status" in got and got.get("status") != "granted":
            return None
        return got

    def consent_card(self, scope: dict, card: dict, floor: bool = False) -> dict:
        """Raise one card that shows exactly *card* (the store's L1 form, so the one card view lists what it
        says). ``{consent_id, card_sha}``. No store, or one without that form: NotBuilt (no card, no Yes)."""
        store = self._consents()
        request = getattr(store, "request", None)
        if not callable(request):
            raise NotBuilt("The consent cards", "L4")
        from ...consent import card_sha
        cid = request(dict(scope), card, floor)
        if not isinstance(cid, str) or not cid:
            raise NotBuilt("The consent cards", "L4")
        rec = store.get(cid) if hasattr(store, "get") else None
        sha = (rec or {}).get("card_sha") if isinstance(rec, dict) else None
        return {"consent_id": cid, "card_sha": str(sha or card_sha(card))}

    def consent_state(self, cid: str) -> dict:
        """``{status, card_sha, scope, card}`` of any card (pending, granted, declined, expired, void...).
        A Yes counts only when the store's own check says it is live (``granted``). Unknown: ``gone``."""
        store = self._consents()
        got = store.get(cid) if hasattr(store, "get") else None
        if not isinstance(got, dict):
            return {"status": "gone"}
        status = str(got.get("status") or "")
        if status == "granted":
            live = store.granted(cid) if hasattr(store, "granted") else got
            status = "granted" if isinstance(live, dict) else "damaged"
        return {"status": status, "card_sha": got.get("card_sha"), "scope": got.get("scope"),
                "card": got.get("card")}

    def consent_expire(self, cid: str, note: str = "") -> None:
        """End a card still waiting (the try it was for ended). Best effort: an answered card stays."""
        try:
            store = self._consents()
            expire = getattr(store, "expire", None)
            if callable(expire):
                expire(cid, note)
        except Exception:  # noqa: BLE001 - the card expires on its own after 10 minutes anyway
            logger.info("card %s not expired", cid)

    def consent_revoke(self, cid: str) -> bool:
        """Take back a Yes the Core no longer acts on (a replaced plan's). Only narrows; best effort."""
        try:
            revoke = getattr(self._consents(), "revoke", None)
            return bool(revoke(cid)) if callable(revoke) else False
        except Exception:  # noqa: BLE001 - the build refuses to start anyway (replaced_by)
            logger.info("card %s not revoked", cid)
            return False

    def held_fingerprints(self, folder: Path | str, rels: list[str]) -> dict[str, str]:
        """Drawbridge's fingerprint of each held file (``zone3.drawbridge.held_fingerprints``)."""
        fn = _need("zone3.drawbridge.held_fingerprints", "Drawbridge's file fingerprints", "L4")
        return dict(fn(str(folder), list(rels)))

    # -- her cloud model Yes (D35): the one record the Bridge opens toolsmith:cloud from ---------
    #
    # Lane I8a, from the owner's live test on 10-25: her wizard choice of a company was kept only in
    # settings\toolsmith.json, so no ``cloud`` Yes record existed, the toolsmith:cloud switch was never
    # turned on, and every call to the company was refused ("switched_off"). Her Yes to the company's own
    # consent sentence is now that record: kind ``cloud``, target ``toolsmith:cloud``, covering only that
    # company's model hosts, in the build phase. The Bridge turns its switch on at her Yes and makes its
    # grant only while a build runs (``Bridge.open_build`` / ``end_build``).

    CLOUD = "toolsmith:cloud"

    def _cloud_store(self) -> tuple[Any, Any]:
        """The consent store the Bridge listens on, and the Bridge. Neither there: NotBuilt (nothing opens)."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        if bridge is None:
            raise NotBuilt(self.zone3_line("The Bridge"), "L4")
        store = getattr(z, "consents", None) or self._consents()
        return store, bridge

    def _cloud_records(self, store: Any) -> list[dict]:
        live = getattr(store, "live", None)
        rows = live("cloud") if callable(live) else []
        return [r for r in rows if (r.get("scope") or {}).get("target") == self.CLOUD]

    def cloud_yes(self, provider_id: str, hosts: list[str], consent_sha: str, by: str = "owner") -> str:
        """Record her Yes to *provider_id*'s consent sentence (its sha). Every earlier cloud Yes is taken
        back first, so only the hosts of the company she picked now can ever open. Returns its consent id."""
        store, bridge = self._cloud_store()
        self._cloud_revoke(store, bridge)
        covers = {"hosts": list(hosts), "phase": "build", "provider": str(provider_id),
                  "consent_sha": str(consent_sha)}
        card = store.ask("cloud", self.CLOUD, covers)
        rec = store.answer(card["consent_id"], "yes", "tap", by=str(by or "owner")[:64], card_sha=card["card_sha"])
        if rec.get("status") != "granted":
            raise NotBuilt("Your Yes for the cloud model", "L4")
        if not (bridge.posture().get("switches") or {}).get(self.CLOUD):
            bridge.set_switch(self.CLOUD, True)          # her Yes turns its switch on (the Bridge's own listener does too)
        return str(card["consent_id"])

    def cloud_off(self) -> int:
        """She chose this computer: every cloud Yes is taken back and toolsmith:cloud closes (grants and switch).
        With no Bridge running, the Yes records are still taken back (nothing goes out without a Bridge)."""
        try:
            store, bridge = self._cloud_store()
        except NotBuilt:
            try:
                store = self._consents()
            except NotBuilt:
                return 0
            return self._cloud_revoke(store, None)
        n = self._cloud_revoke(store, bridge)
        bridge.forget(self.CLOUD)
        return n

    def _cloud_revoke(self, store: Any, bridge: Any) -> int:
        gone = []
        for rec in self._cloud_records(store):
            if store.revoke(rec["consent_id"]):
                gone.append(rec["consent_id"])
        if gone and bridge is not None:
            try:
                bridge.policy.drop_where(lambda g: g.get("subject") == self.CLOUD and g.get("consent") in gone)
            except Exception:  # noqa: BLE001 - a grant whose Yes was taken back never allows anything anyway
                logger.info("old cloud grants not dropped")
        return len(gone)

    def cloud_live(self, provider_id: str, consent_sha: str) -> bool:
        """Is there a live cloud Yes for exactly this company and this consent sentence? Can't tell: no."""
        try:
            store, _bridge = self._cloud_store()
            return any((r.get("covers") or {}).get("provider") == provider_id
                       and (r.get("covers") or {}).get("consent_sha") == consent_sha
                       for r in self._cloud_records(store))
        except Exception:  # noqa: BLE001 - fail closed
            return False

    # -- the Bridge's build grants (Z3 section 2: they live only while Toolsmith works) --------

    def bridge_build_open(self, build_id: str, plan_consent: str | None) -> None:
        """Toolsmith's process for *build_id* starts: its build grants open from her live Yes records."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        fn = getattr(bridge, "open_build", None)
        if callable(fn):
            fn(build_id, plan_consent=plan_consent)

    def bridge_build_end(self, build_id: str) -> None:
        """Toolsmith's process for *build_id* ended (any way): its build grants close."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        fn = getattr(bridge, "end_build", None)
        if callable(fn):
            fn(build_id)

    def bridge_knowledge_end(self, build_id: str) -> None:
        """This build's knowledge grants close (``Bridge.close_knowledge``), even when ``end_build`` failed."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        fn = getattr(bridge, "close_knowledge", None)
        if callable(fn):
            fn(build_id)

    # -- L3: Archivist and the outbound screen ----------------------------------

    def _archivist(self) -> Any:
        for_state = find("zone1.archivist.for_state")
        if for_state is None:
            raise KeyUnavailable("not_built", "The vault keeper is not built yet (lane L3), "
                                              "so Toolsmith can't borrow its cloud key.")
        return for_state(self.state, self.bus)

    def lend_key(self, key_name: str, borrower: str, purpose: str, *, hosts: tuple = (),
                 job: str | None = None) -> str:
        """A lease handle for *key_name*, for these hosts and this job, never the bytes (D33, D41)."""
        a = self._archivist()
        try:
            handle = a.lend(key_name, borrower, purpose, hosts=tuple(hosts), job=job)
        except Exception as exc:  # noqa: BLE001 - its reason, in plain words
            reason = _vault_reason(exc)
            line = {"locked": "Unlock your vault to let Toolsmith go on.",
                    "missing": "Toolsmith's cloud key isn't saved yet."}.get(reason, "The vault did not lend Toolsmith its key.")
            raise KeyUnavailable("refused" if reason == "ended" else reason, line) from None
        if not handle:
            raise KeyUnavailable("missing", "Toolsmith's cloud key isn't saved yet.")
        return str(handle)

    def key_value(self, handle: str, *, borrower: str, purpose: str, host: str | None = None) -> str:
        """The key bytes for one send to *host*, inside the Core sender only. Never stored."""
        a = self._archivist()
        try:
            value = a.use(handle, borrower=borrower, purpose=purpose, host=host)
        except Exception as exc:  # noqa: BLE001
            raise KeyUnavailable(_vault_reason(exc), "Toolsmith's cloud key lease can't be used.") from None
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8")
        if not value:
            raise KeyUnavailable("missing", "Toolsmith's cloud key isn't there any more.")
        return str(value)

    def vault_open(self) -> bool:
        """Is the vault open now? Read only: never unlocks and never counts as vault use (the idle timer)."""
        return bool(self._archivist().is_open())

    def lease_live(self, handle: str) -> bool:
        """Is this lease still live (not ended, not past its 3 hours)? Never reads the key."""
        try:
            return bool(self._archivist().live(handle))
        except Exception:  # noqa: BLE001 - can't tell: not live
            return False

    def end_lease(self, handle: str) -> None:
        """The build ended: end its key lease now (D41), not at the 3 hour cap."""
        try:
            self._archivist().end(handle, "build finished")
        except KeyUnavailable:
            pass

    def _people(self) -> list[dict]:
        """The Core's people list (D12), for the outbound screen (a child's name is floor-level data)."""
        try:
            from ...people import load
            got = load(self.state)
            return [p for p in got if isinstance(p, dict)] if isinstance(got, list) else []
        except Exception:  # noqa: BLE001 - no list: the screen still runs on everything else
            return []

    def household_names(self) -> list[str]:
        """Every name the knowledge topics must never carry (lane I11): the people list, the owner's own name
        and what she is called from the "You" settings (lane I12), and her account name. A people list or account
        name that can't be read adds nothing; You data that is kept but can't be read (the vault is locked) raises,
        so no topic is sent (fail closed, lane I14)."""
        names = [str(p.get("name") or "") for p in self._people()]
        names += [str(n) for n in you_names(self.state)]
        try:
            from ..toolsmith.names import account_names
            names += list(account_names())
        except Exception:  # noqa: BLE001
            pass
        return sorted({n.strip() for n in names if n and n.strip()})

    def screen_outbound(self, text: str) -> str:
        """*text* with every key and personal detail the outbound classifier finds taken out (X20)."""
        classify = _need("zone1.classify.classify", "The personal-details screen", "L3")
        found = classify(text, path="outbound", people=self._people())
        if getattr(found, "checked", "full") != "full":
            raise Refused("The privacy check could not read all of it.")
        out, at = [], 0
        for g in sorted(found.groups(), key=lambda x: x.start):
            if g.end <= at:
                continue
            out.append(text[at:max(at, g.start)])
            out.append("[key]" if getattr(g, "secret", False) else "[personal detail]")
            at = g.end
        out.append(text[at:])
        return "".join(out)

    # -- L4: Toolsmith's zone, Drawbridge, the Bridge, the Docker wall ----------

    def zone_check(self, target: str, *, kind: str, folder: Path | str, write: bool = False,
                   cwd: Path | str = "", logs: Path | str = "", scratch: Path | str = "",
                   pids: Any = (), owner_step: bool = False) -> ZoneAnswer:
        """L4's ``toolsmith_zone.check``: allow, deny or ask. Missing, crashed or odd = deny."""
        check = find("zone3.toolsmith_zone.check")
        if check is None:
            return ZoneAnswer("deny", "Toolsmith's zone check is not built yet (lane L4), so nothing runs.")
        try:
            got = check(str(target), folder=str(folder), kind=kind, write=write, cwd=str(cwd or ""),
                        logs=str(logs or ""), scratch=str(scratch or ""), own_pids=tuple(pids or ()),
                        owner_step=bool(owner_step))
        except Exception as exc:  # noqa: BLE001 - a crashed check is a no (X26)
            return ZoneAnswer("deny", f"Toolsmith's zone check did not answer ({type(exc).__name__}), "
                                      "so this was refused.")
        verdict = str(getattr(got, "verdict", "") or "")
        if verdict not in VERDICTS:
            return ZoneAnswer("deny", "Toolsmith's zone check gave an answer PuffPAI does not know, "
                                      "so this was refused.")
        line = str(getattr(got, "line", "") or "")
        if verdict == "deny" and not line:
            line = "That is outside what Toolsmith may touch."
        outside = tuple(str(x) for x in (getattr(got, "outside", ()) or ()))
        return ZoneAnswer(verdict, line, outside)

    def drawbridge_check(self, folder: Path | str, manifest: dict | None, *, origin: str = "local") -> dict:
        """Drawbridge's verdict dict (``verdict``, ``reasons``, ``tree_sha``, ``line``); missing = block."""
        z = self.zone3()
        db = getattr(z, "drawbridge", None) if z is not None else None
        if db is None or not hasattr(db, "check_package"):
            return {"verdict": "block", "reasons": ["not_built"], "tree_sha": "",
                    "line": self.zone3_line("Drawbridge") + ", so nothing is checked or run."}
        try:
            got = db.check_package(str(folder), manifest if isinstance(manifest, dict) else {}, origin=origin)
        except Exception as exc:  # noqa: BLE001 - no check means no
            return {"verdict": "block", "reasons": ["check_failed"], "tree_sha": "",
                    "line": f"Drawbridge's check did not finish ({type(exc).__name__})."}
        if not isinstance(got, dict):
            return {"verdict": "block", "reasons": ["odd_answer"], "tree_sha": "",
                    "line": "Drawbridge gave an answer PuffPAI does not know."}
        return got

    def verify_tree(self, folder: Path | str, verdict: dict) -> bool:
        """True only if *verdict* passed and the folder is byte-for-byte what was checked (X24), under the
        owner's setting for files Drawbridge can't read now (D95: the Core's own Drawbridge knows it)."""
        z = self.zone3()
        own = getattr(getattr(z, "drawbridge", None), "verify", None) if z is not None else None
        verify = own if callable(own) else find("zone3.drawbridge.verify_tree")
        if verify is None:
            return False
        try:
            return verify(str(folder), verdict) is True
        except Exception:  # noqa: BLE001 - no proof means no
            return False

    def hide_ai_lines(self, text: str) -> str:
        """*text* with lines aimed at AIs hidden (Drawbridge's text rules)."""
        hide = _need("zone3.text_rules.hide_ai_lines", "Drawbridge's README screen", "L4")
        got = hide(text)
        return str(got[0] if isinstance(got, tuple) else got)

    def screen_knowledge(self, text: str) -> tuple[str | None, str]:
        """Outside text for her bot's knowledge, through Drawbridge's text rules (as a README is): clearly
        hostile text is not kept (``(None, why)``); lines aimed at an AI are hidden; hidden and control
        characters go. ``(text, "")`` when it may be kept. No screen here: NotBuilt (nothing is kept)."""
        block = _need("zone3.text_rules.readme_block", "Drawbridge's text screen", "L4")
        hide = _need("zone3.text_rules.hide_ai_lines", "Drawbridge's text screen", "L4")
        why = block(text)
        if why:
            return None, str(why)
        got = hide(text)
        return strip_hidden(str(got[0] if isinstance(got, tuple) else got)), ""

    def knowledge_index(self, path: Path | str) -> str:
        """Index one fetched file now, through the Library's ``scan_file``. Since lane I18d a file the Core's
        fetch wrote, whose path and bytes match its record in ``<state>/index/fetched.json``
        (``zone1.fetched.written_by_fetch``), is public text and is indexed whole; any other file gets the full
        privacy screen (held values to the vault, handles in the index). "" when it is searchable; else the
        plain reason (the next search's scan tries again)."""
        archivist = find("seams.archivist")
        a = archivist(self.state, self.bus) if archivist is not None else None
        if a is None:
            return "the vault keeper is not here yet, so it is indexed at the next search"
        from ...zone1.folders import Library
        got = Library(a, self.state).scan_file(Path(path))
        return "" if got.get("status") in ("indexed", "unchanged") else str(got.get("reason") or "not indexed")

    def knowledge_search(self, query: str, limit: int) -> list[dict]:
        """The brain's own ``search_knowledge`` search (``Library.search``, the index only, no network):
        ``[{path, chunk, snippet}]``. Lane I19e: a job proved by the library (D71)."""
        archivist = find("seams.archivist")
        a = archivist(self.state, self.bus) if archivist is not None else None
        if a is None:
            return []
        from ...zone1.folders import Library
        return Library(a, self.state).search(query, limit)

    def bridge_decision(self, subject: str, host: str, phase: str = "build") -> str:
        """``allow`` | ``deny`` | ``ask`` from the Bridge (``Bridge.allowed``). Missing = deny."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        if bridge is None or not hasattr(bridge, "allowed"):
            return "deny"
        try:
            got = bridge.allowed(subject, host, [], phase=phase)
        except Exception:  # noqa: BLE001 - no decision means no
            return "deny"
        verdict = str(getattr(got, "verdict", "") or "")
        return verdict if verdict in VERDICTS else "deny"

    def bridge_allowed(self, subject: str, host: str, phase: str = "build") -> bool:
        return self.bridge_decision(subject, host, phase) == "allow"

    def bridge_peek(self, subject: str, host: str, phase: str = "build") -> bool:
        """Would the Bridge allow this now? ``Bridge.peek``: nothing is sent, so no line in the outside log."""
        z = self.zone3()
        peek = getattr(getattr(z, "bridge", None), "peek", None) if z is not None else None
        try:
            return callable(peek) and peek(subject, host, phase) is True
        except Exception:  # noqa: BLE001 - no decision means no
            return False

    def search_yeses(self) -> list[dict] | None:
        """Her live ``host`` Yes records for Toolsmith's search, or None when they can't be read (then no API she added
        counts). The Core's own check at send time, the way the Bridge checks her cloud model Yes."""
        try:
            store = getattr(self.zone3(), "consents", None) or self._consents()
            return [r for r in store.live("host") if (r.get("scope") or {}).get("target") == "toolsmith:search"]
        except Exception:  # noqa: BLE001 - can't tell is no Yes
            return None

    def search_places(self) -> list[str]:
        """The search hosts (``policy.SEARCH_HOSTS``) this bot's search Yes covers right now: a live grant for
        ``toolsmith:search`` on that host, read without a line in the outside log. Can't tell: none."""
        from ..policy import SEARCH_HOSTS
        return [h for h in SEARCH_HOSTS if self.bridge_peek("toolsmith:search", h)]

    def proxy_env(self, name: str, subject: str, phase: str = "install") -> dict:
        """The Bridge proxy's variables, with a fresh credential for *subject* (a try's own)."""
        z = self.zone3()
        creds = getattr(z, "credentials", None) if z is not None else None
        env_of = find("zone3.proxy.proxy_env")
        if creds is None or env_of is None or not hasattr(creds, "issue"):
            exc = NotBuilt("The Bridge proxy", "L4")
            exc.args = (self.zone3_line("The Bridge proxy") + ", so nothing runs that could go around it.",)
            raise exc
        url = creds.issue(name, subject=subject, phase=phase)
        proxy = getattr(z, "proxy", None)
        port = getattr(proxy, "port", None)
        if isinstance(port, int) and not url.endswith(f":{port}"):
            url = url.rsplit(":", 1)[0] + f":{port}"
        got = env_of(url)
        return dict(got) if isinstance(got, dict) else {}

    def bridge_refused(self, subject: str, after: int = 0) -> tuple[list[dict], int]:
        """The hosts the Bridge refused for *subject* since the mark *after* (the Bridge ledger's
        ``refused_since``), and the next mark: ``([{host, code}], mark)``. Read only, host names and closed
        codes only (D70). Not running or an odd answer: none, and the mark stays."""
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        read = getattr(getattr(bridge, "ledger", None), "refused_since", None)
        if read is None:
            return [], after
        try:
            rows, mark = read(str(subject), int(after))
        except Exception:  # noqa: BLE001 - only words for Toolsmith: none is safe
            return [], after
        from ..ledger import REASON_CODES as codes, safe_host as safe
        out = []
        for r in rows if isinstance(rows, list) else []:
            host = r.get("host") if isinstance(r, dict) else None
            if isinstance(host, str) and host and safe(host) == host:
                code = r.get("code")
                out.append({"host": host, "code": code if code in codes else ""})
        return out, mark if isinstance(mark, int) and mark >= after else after

    def docker_override(self, manifest: dict, compose_path: Path | str) -> Path:
        """The Docker wall's override file for one compose file (D30). Refused = ``Refused``."""
        override = _need("zone3.docker_wall.override", "The Docker wall", "L4")
        wall_kw = find("zone3.docker_wall.wall_kw")
        proxy = getattr(self.zone3(), "proxy", None)
        try:
            # The running proxy's real port and, on plain Linux Docker, its second listener: as Steward's start.
            got = override(manifest, compose_path, **(wall_kw(proxy) if wall_kw is not None and proxy is not None else {}))
        except ValueError as exc:                       # WallRefused is a ValueError
            problems = getattr(exc, "problems", None) or [str(exc)]
            raise Refused("The Docker wall refused this app: " + " ".join(str(p) for p in problems)) from None
        except RuntimeError:                            # "Zone 3 is not running in this Core."
            raise Refused(self.zone3_line("The Bridge") + ", so the Docker wall can't be set up.") from None
        if not isinstance(got, (str, Path)) or not Path(got).is_file():
            raise Refused("The Docker wall gave no override, so this app is not started in Docker.")
        return Path(got)

    def docker_gate_problem(self, run: Callable[[list[str]], int]) -> str:
        """After a try's ``docker compose up``: the plain line when its gate can't reach the Bridge proxy, else
        ``""``. Only plain Linux Docker can have this (``docker_wall.gate_problem``). *run* runs ``docker compose
        <args>`` for that try and gives the exit code."""
        wall_kw, check = find("zone3.docker_wall.wall_kw"), find("zone3.docker_wall.gate_problem")
        proxy = getattr(self.zone3(), "proxy", None)
        if wall_kw is None or check is None or proxy is None:
            return ""
        try:
            kw = wall_kw(proxy)
        except ValueError as exc:                       # WallRefused: the engine went, or the address did
            return str(exc)
        return check(run, kw["host_proxy_port"]) if kw.get("host_gateway") else ""

    # -- L2: the brain --------------------------------------------------------

    def local_turn(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """One turn on this computer's model, in the neutral shape (04 quick start 11)."""
        turn = _need("zone2.brain.toolsmith_turn", "Toolsmith's model on this computer", "L2")
        return turn(system, messages, tools)

    def brain_picks(self, package: str, ask: str, store: list | None = None) -> dict:
        """Does the local brain pick one of *package*'s tools for *ask* (a job's words)? ``{ok, called, reason}``.
        Raises NotBuilt when there is no brain to ask; a brain that can't answer is ``{ok: False, asked: False}``."""
        # The running Core's own Brain (its Steward's tokens reach the packages' tools); else the module's.
        picks = getattr(self, "picker", None) or _need("zone2.brain.picks", "The brain's tool check", "L2")
        try:
            # *store*: the PuffPAI store's apps, whose listed tools join the brain's table (lane I10c)
            got = picks(package, ask) if store is None else picks(package, ask, store=store)
        except Exception as exc:  # noqa: BLE001 - BrainError and friends: it was not asked
            return {"ok": False, "asked": False, "called": [],
                    "reason": getattr(exc, "message", "") or str(exc) or type(exc).__name__}
        return dict(got, asked=True) if isinstance(got, dict) else {"ok": False, "asked": False, "called": [],
                                                                   "reason": "no answer"}

    def brain_json(self, system: str, user: str) -> Any:
        """A small JSON ask of this computer's model, or None (never a veto)."""
        ask = find("zone2.brain.ask_json")
        if ask is None:
            return None
        try:
            return ask(system, user)
        except Exception:  # noqa: BLE001 - no answer: the caller's fallback
            return None

    # -- lane I17: the model roles (``zone2.roles``), for a plan's model offer -----------------------

    def model_needs(self, goal: str, jobs: list[dict]) -> list[str]:
        """The roles beyond chat this goal needs, from one small JSON ask of this computer's brain."""
        from ...zone2 import roles
        return roles.needs(self.brain_json, goal, jobs)

    def model_offer(self, role: str) -> dict | None:
        """What a plan card offers for *role*: the best fitting catalog model, or a plain "none fits"."""
        from ...zone2 import roles
        return roles.offer(self.state, role)

    def model_installed(self, model: str) -> bool:
        from ...zone2 import models
        from ...zone2.catalog import is_installed
        return is_installed(model, models.installed(self.state))

    def model_card(self, model: str) -> dict:
        """Raise one download Yes card for a catalog model that fits this computer: ``{consent_id, card_sha}``."""
        from ...zone2 import models, roles
        row = next((r for r in roles.rows(roles.census(self.state), models.installed(self.state))
                    if r["id"] == model and r["fits"]), None)
        if row is None:
            raise Refused("That model is not in the list, or it does not fit this computer.")
        scope, card = roles.pull_card(row)
        return self.consent_card(scope, card)

    def model_pull(self, model: str) -> tuple[bool, str]:
        """Ask the local Ollama to download *model* (blocking). Only after its own Yes card."""
        from ...zone2 import roles
        return roles.pull_now(self.state, model)

    def model_set_role(self, role: str, model: str) -> None:
        from ...zone2 import models, roles
        roles.set_role(self.state, role, model, models.installed(self.state))

    def read_page(self, url: str) -> dict:
        """``{ok, title, text, said}`` for a page on 127.0.0.1."""
        read = _need("zone2.apps.read_page", "The page reader", "L2")
        got = read(url)
        return got if isinstance(got, dict) else {"ok": False, "said": "no answer"}

    def read_app(self, pkg_id: str, path: str = "") -> dict:
        """What the brain's ``read_app`` returns for an installed package."""
        read_app = _need("zone2.apps.read_app", "The brain's read_app", "L2")

        def find_row(i: str):
            row = self.registry.get(i) if self.registry is not None else None
            if row is None:
                return None
            # The Core's one reader of an installed package's manifest (her Yes, by its sha256).
            reader = getattr(self.steward, "read_manifest", None)
            try:
                man = reader(i) if callable(reader) else None
            except Exception:  # noqa: BLE001 - no manifest: nothing it names is used
                man = None
            return row, man
        return read_app(find_row, None, {"id": pkg_id, "path": path})

    def brain_answer(self, question: str, context: str) -> str:
        answer = _need("zone2.brain.answer", "The brain's answer", "L2")
        return str(answer(question, context) or "")

    # -- L5: the Checklist ----------------------------------------------------

    def checklist_add(self, step: dict, package: str, blocks: list[str]) -> dict:
        """File a step the Core raises for Toolsmith (L5 ``Checklist.add``, trust ``core``)."""
        if self.checklist is None or not hasattr(self.checklist, "add"):
            raise NotBuilt("The Checklist", "L5")
        try:
            return self.checklist.add(step, package, blocks, trust="core", raised_by="toolsmith")
        except ValueError as exc:                       # ChecklistError: its plain words
            raise Refused(str(exc)) from None

    def checklist_state(self, step_id: str) -> str:
        """``open`` | ``done`` | ``skipped`` (``gone`` when it is not there)."""
        if self.checklist is None or not hasattr(self.checklist, "get"):
            raise NotBuilt("The Checklist", "L5")
        got = self.checklist.get(step_id)
        if not got:
            return "gone"
        return str(got.get("state") or got.get("status") or "open")

    # -- the Core's own outside calls, always behind the Bridge ----------------

    def http_send(self, method: str, url: str, *, subject: str, headers: dict | None = None,
                  body: bytes | None = None, max_bytes: int = 2_000_000,
                  timeout: float = 60.0, phase: str = "build",
                  opener: Callable[[], Any] | None = None) -> tuple[int, dict, bytes]:
        """One HTTPS call, only when the Bridge allows *subject* to reach its host.

        ``(status, headers, body)``. Raises ``Refused`` (the Bridge said no or
        asks first), ``Redirected`` (a 3xx: never followed, so no second host
        and no forwarded key) or ``OSError`` (unreachable, or :class:`TooSlow`:
        a GET past *timeout* seconds in all, another call's reply still coming
        *timeout* seconds after its headers). The Core sends it itself
        (``via: core``); the User-Agent is generic and no identifier is added.
        *opener*: makes the opener, only after the Bridge said yes and inside the time limit (:meth:`owner_get`)."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not parts.hostname:
            raise Refused("Toolsmith only sends to https addresses.")
        decision = self.bridge_decision(subject, parts.hostname, phase)
        if decision == "ask":
            raise Refused(f"The Bridge needs your Yes for {parts.hostname} first, so nothing was sent.")
        if decision != "allow":
            raise Refused(f"The Bridge is closed for {parts.hostname}, so nothing was sent.")
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"User-Agent": USER_AGENT, **(headers or {})})
        host = parts.hostname

        def exchange() -> tuple[int, dict, bytes]:
            began = time.monotonic()
            try:
                with (opener or self._opener)().open(req, timeout=timeout) as reply:
                    start = began if method == "GET" else time.monotonic()
                    return reply.status, dict(reply.headers.items()), _read_within(reply, max_bytes, start + timeout,
                                                                                   host, timeout)
            except urllib.error.HTTPError as exc:
                start = began if method == "GET" else time.monotonic()
                return exc.code, dict(exc.headers.items()) if exc.headers else {}, \
                    _read_within(exc, max_bytes, start + timeout, host, timeout)
        status, got, data = _within(timeout, host, exchange) if method == "GET" else exchange()
        if 300 <= int(status) < 400:
            moved = Redirected(parts.hostname, int(status))
            moved.location = next((str(v) for k, v in got.items() if k.lower() == "location"), "")
            raise moved
        return status, got, data

    def _opener(self) -> urllib.request.OpenerDirector:
        """No proxy (the Core sends after the Bridge's own yes) and no redirects, ever."""
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), *self._handlers())

    def _resolve(self, host: str) -> list[str]:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]

    def _pinned_opener(self, host: str, guard: Callable[[], None] | None = None) -> urllib.request.OpenerDirector:
        """An opener that reaches *host* only on addresses checked just now. Any answer that is not public refuses the
        send, IPv6 and mapped forms included (the proxy's own check, ``proxy.answer_refusal``). A lookup can outlast the
        caller: *guard* runs once it is back, and again at the connection."""
        from ..proxy import answer_refusal
        try:
            addresses = tuple(dict.fromkeys(self._resolve(host)))
        except OSError:
            raise NotPublic(f"{host} could not be looked up, so nothing was sent.", "cannot_look_up") from None
        why = answer_refusal(list(addresses))
        if why == "cannot_look_up":
            raise NotPublic(f"{host} could not be looked up, so nothing was sent.", why)
        if why:
            raise NotPublic(f"{host} points at a private address, so nothing was sent.", why)
        if guard is not None:
            guard()
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(), *self._handlers(),
                                           _PinnedHTTPS(addresses, guard))

    def _handlers(self) -> tuple:
        """Extra urllib handlers (tests give a recorded HTTPS one here)."""
        return ()

    def http_get(self, url: str, *, subject: str = "toolsmith:search", accept: str = "",
                 max_bytes: int = 2_000_000, headers: dict | None = None,
                 timeout_s: float | None = None) -> tuple[int, dict, bytes]:
        headers = {**({"Accept": accept} if accept else {}), **(headers or {})}
        return self.http_send("GET", url, subject=subject, headers=headers, max_bytes=max_bytes,
                              timeout=30.0 if timeout_s is None else timeout_s)

    def owner_get(self, url: str, *, subject: str = "toolsmith:search", phase: str = "build", accept: str = "",
                  max_bytes: int = 1_000_000, headers: dict | None = None, key_query: dict | None = None,
                  timeout_s: float = 10.0) -> tuple[int, dict, bytes]:
        """One search on an API she added: the Bridge first, then the address checked and pinned (:meth:`_pinned_opener`),
        no redirect. *key_query*: a key that goes in the address, added here, past every check and every log: the address
        with it is never returned, and no error this path raises names more than the host."""
        host = urllib.parse.urlsplit(url).hostname or ""
        sent = url
        if key_query:
            sent += ("&" if urllib.parse.urlsplit(url).query else "?") \
                + urllib.parse.urlencode(key_query, quote_via=urllib.parse.quote, safe="")
        deadline = time.monotonic() + timeout_s

        def still_wanted() -> None:
            """The worker may run on after the caller gave up and took its Yes back: its deadline and the Bridge, again."""
            if time.monotonic() >= deadline:
                raise TooSlow(host, timeout_s)
            if not self.bridge_peek(subject, host, phase):
                raise Refused(f"The Bridge closed for {host} before this went out, so nothing was sent.")
        return self.http_send("GET", sent, subject=subject, phase=phase, max_bytes=max_bytes, timeout=timeout_s,
                              headers={**({"Accept": accept} if accept else {}), **(headers or {})},
                              opener=lambda: self._pinned_opener(host, still_wanted))

    def public_get(self, url: str, *, build_id: str, consent: dict, max_bytes: int = 2_000_000,
                   timeout_s: float = 20.0) -> tuple[int, dict, bytes]:
        """One hop of a cloud build's read of a public page (D121), under its own subject: the host named from her plan
        Yes *consent*, the Bridge's decision and its line in the outside log, then :meth:`owner_get`'s address check and
        pin. GET, no redirect (:class:`Redirected`), and a fixed Accept and the generic User-Agent as its only headers:
        no cookie, no ``Authorization``. No Bridge here: its decision is no, so nothing is sent."""
        from ..policy import READ_SUBJECT
        z = self.zone3()
        bridge = getattr(z, "bridge", None) if z is not None else None
        host = urllib.parse.urlsplit(url).hostname or ""
        name = getattr(bridge, "open_read", None)
        if callable(name):
            name(build_id, host, consent)
        try:
            return self.owner_get(url, subject=READ_SUBJECT, accept=PUBLIC_ACCEPT, max_bytes=max_bytes, timeout_s=timeout_s)
        except NotPublic as exc:
            self.public_refused(host, exc.code)
            raise

    def public_refused(self, host: str, code: str) -> None:
        """A public read the Core stopped at the address has its own line in the outside log (D121). Host and code only."""
        from ..policy import READ_SUBJECT
        z = self.zone3()
        record = getattr(getattr(getattr(z, "bridge", None), "ledger", None), "record", None) if z is not None else None
        if callable(record):
            record(READ_SUBJECT, host=host, port=443, outcome="refused", code=code, via="core", phase="build", method="GET")

    @contextlib.contextmanager
    def one_send_yes(self, host: str, by: str = "owner") -> Iterator[str]:
        """Her pressing Test is her Yes for one send to *host*, recorded the way :meth:`cloud_yes` records her choice, and
        taken back the moment it ends. It stands on its own subject, so Toolsmith's search switch is never touched and
        no Toolsmith send can use it. The Bridge still decides: closed, blocked or Drawbridge down refuses, and the send
        has its line in the outside log."""
        from ..bridge import OWNER_TEST_SUBJECT as SUBJECT
        store, bridge = self._cloud_store()
        if bridge.view({}).get("setting") != "open":
            raise Refused("The Bridge is closed, so nothing was sent. Open it, then press Test again.")
        with _ONE_SEND:
            bridge.forget(SUBJECT)                       # anything a crash left
            ends = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(timespec="seconds")
            card = store.ask("host", SUBJECT, {"hosts": [host], "phase": "run"}, expires=ends)
            rec = store.answer(card["consent_id"], "yes", "tap", by=str(by or "owner")[:64], card_sha=card["card_sha"])
            try:
                if rec.get("status") != "granted":
                    raise Refused("BuildBot could not record your Test, so nothing was sent.")
                yield SUBJECT
            finally:
                try:
                    store.revoke(card["consent_id"])
                finally:
                    bridge.forget(SUBJECT)


__all__ = ["KNOWLEDGE_USER_AGENT", "KeyUnavailable", "NotPublic", "PUBLIC_ACCEPT", "Ports", "Redirected", "TooSlow", "USER_AGENT", "VERDICTS", "ZoneAnswer", "find",
           "strip_hidden", "you_names", "zone3"]
