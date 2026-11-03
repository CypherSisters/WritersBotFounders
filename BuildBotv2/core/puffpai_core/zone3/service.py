"""Zone 3 in one object: the Bridge, Drawbridge, the consent record, the proxy and the watches.

One per running Core. The Core API groups ``bridge``, ``consent`` and
``audit`` reach it with :func:`for_app`; other lanes use :func:`current`.

    z = Zone3(state, bus=bus, registry=registry)
    z.start_proxy()                      # 127.0.0.1:8421 (lane L1 starts it with the Core)
    env = z.package_env("freshtable")    # HTTP(S)_PROXY with a fresh credential, for Steward
    z.start_watch(steward)               # stop on bypass (D26), Steward from lane L1
    z.bridge.allowed("pkg:freshtable", "api.example.com", [])
    z.drawbridge.check_package(folder, manifest, origin="adopted:https://...")
    z.stop()                             # stops the proxy and the watch
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .. import anchor as _anchor
from ..bus import Bus
from ..consent import ConsentStore
from .bridge import Bridge
from .bypass import BypassWatch
from .chain import ChainLog
from .vault_anchor import AUDIT, Archivist, VaultCheck
from . import addresses, files_mode
from .drawbridge import Drawbridge
from .policy import default_share
from .proxy import PORT, Credentials, Proxy, proxy_env

_CURRENT: "Zone3 | None" = None


class Cards(list):
    """Zone 3's cards: a list that tells each of ``listeners`` about every card added (the Core's wiring
    routes them to the Desktop notices and the terminal). A listener that raises is skipped."""

    def __init__(self) -> None:
        super().__init__()
        self.listeners: list[Callable[[dict], None]] = []

    def append(self, card: dict) -> None:  # type: ignore[override]
        super().append(card)
        for fn in list(self.listeners):
            try:
                fn(card)
            except Exception:  # noqa: BLE001 - the card is still on the list
                pass


class Zone3:
    def __init__(self, state: Path | str, *, bus: Bus | None = None, registry: Any = None,
                 consents: ConsentStore | None = None, verify_pin: Callable[[str], bool] | None = None,
                 machine: Any = None, share_of: Callable[[str], str] = default_share,
                 archivist: Archivist | None = None) -> None:
        global _CURRENT
        self.state = Path(state)
        self.bus = bus or Bus()
        self.registry = registry
        self.cards = Cards()
        self.anchor = _anchor.for_state(self.state, machine)
        # D54: Archivist's vault copy (lane L3): the Core's own Archivist, else zone1.archivist.for_state.
        self.vault = VaultCheck(archivist, state=self.state, bus=self.bus, anchor=self.anchor)
        self.audit = ChainLog(self.state / "logs" / "audit.jsonl", on_tamper=self._audit_tampered,
                              anchor=self.anchor, seal=self.vault.sealer(AUDIT))
        # One consent store per Core: the Core's own (lane L1 makes it) when there is one.
        self.consents = consents or ConsentStore(self.state / "consent.db", self.bus, anchor=self.anchor,
                                                 verify_pin=verify_pin)
        if verify_pin is not None:
            self.consents.verify_pin = verify_pin
        self.consents.on_generation = self.vault.seal_generation
        self.bridge = Bridge(self.state, self.bus, consents=self.consents, anchor=self.anchor, share_of=share_of,
                             on_card=self.cards.append, vault=self.vault)
        self.bridge.extra_logs = {AUDIT: self.audit}
        has_run = self.bridge.policy.high > 0 or self.bridge.ledger.chain.head()[1] > 0 or self.audit.head()[1] > 0
        self.bridge.vault_at_start(has_run)                         # pending on disk against the files
        self.vault.register(self.vault_unlocked)                   # Archivist.on_unlock (lane L3)
        if not self.vault.wired:                                    # the check runs once per unlock (D151)
            self.bus.subscribe("vault.unlocked", lambda _m: self.vault_unlocked(), "core")   # no Archivist here: closes
        # D95: the owner's setting for files Drawbridge can't read, read at every check (missing: Normal).
        self.drawbridge = Drawbridge(self.audit, files_mode=lambda: files_mode.read(self.state))
        self.consents.on_granted(self._address_yes)
        self.credentials = Credentials()
        self.proxy: Proxy | None = None
        self.watch: BypassWatch | None = None
        _CURRENT = self

    def _address_yes(self, rec: dict) -> None:
        """Her Yes for one address (D110): Drawbridge's scope for that package now counts it as reaching out."""
        package = addresses.package_of(rec)
        if package:
            self.drawbridge.reaches_out(package)

    # -- the proxy --------------------------------------------------------------
    def _proxy_decide(self, *, subject: str, host: str, port: int, phase: str, scheme: str, method: str):
        return self.bridge.request(subject, host, port=port, phase=phase, scheme=scheme, method=method,
                                   labels=self.taint(subject), via="proxy")

    def taint(self, subject: str) -> list[str]:
        """The package's delivery taint (labels of data the Core handed it), from its Registry row."""
        if self.registry is None or not subject.startswith("pkg:"):
            return []
        try:
            row = self.registry.get(subject[4:]) or {}
            return [str(x) for x in row.get("taint") or [] if isinstance(x, str)]
        except Exception:  # noqa: BLE001 - unreadable taint: the strictest reading
            return ["secret.unknown"]

    def start_proxy(self, port: int = PORT) -> int:
        if self.proxy is None:
            self.proxy = Proxy(self._proxy_decide, self.bridge.ledger, self.credentials, port=port)
            self.proxy.start()
        return self.proxy.port

    def package_env(self, package_id: str, *, phase: str = "run") -> dict[str, str]:
        """Proxy variables with a fresh credential (Steward sets them at each start; 02)."""
        url = self.credentials.issue(package_id, phase=phase)
        if self.proxy is not None and self.proxy.port != PORT:
            url = url.rsplit(":", 1)[0] + f":{self.proxy.port}"
        return proxy_env(url)

    # -- the bypass watch -------------------------------------------------------
    def start_watch(self, steward: Any, *, interval: float = 0.5) -> BypassWatch:
        if self.watch is None:
            self.watch = BypassWatch(steward, ledger=self.bridge.ledger, bus=self.bus, registry=self.registry,
                                     on_card=self.cards.append, interval=interval)
            self.watch.start()
        return self.watch

    def stop(self) -> None:
        self.vault.unregister()
        if self.watch is not None:
            self.watch.stop()
            self.watch = None
        if self.proxy is not None:
            self.proxy.stop()
            self.proxy = None

    def vault_unlocked(self) -> list[dict]:
        """At every unlock (D54): the vault's copy against the Bridge policy, the outside log and the audit log."""
        try:
            return self.bridge.vault_unlocked({AUDIT: self.audit})
        except Exception:  # noqa: BLE001 - a check that breaks closes, with a card, never passes
            try:
                self.bridge._vault_problems([{"what": "check", "why": "cant_confirm"}])
            except Exception:  # noqa: BLE001
                try:
                    self.bridge.close(by="core")
                except Exception:  # noqa: BLE001
                    pass
            return [{"what": "check", "why": "cant_confirm"}]

    def _audit_tampered(self, note: dict) -> None:
        if note.get("why") == "no_machine_key":
            return                              # the Bridge already carded it and is closed
        try:
            self.bridge.close(by="core")
        except Exception:  # noqa: BLE001
            pass
        self.cards.append({"kind": "audit_damaged",
                           "line": "The audit log was changed outside PuffPAI, so the Bridge is closed. "
                                   "Check and open it again."})


def current() -> Zone3:
    if _CURRENT is None:
        raise RuntimeError("Zone 3 is not running in this Core.")
    return _CURRENT


def for_app(app: Any) -> Zone3:
    """The app's Zone 3, made once from its Core parts."""
    z = app.extensions.get("puffpai.zone3")
    if z is None:
        core = app.extensions["puffpai"]
        z = Zone3(core.state, bus=core.bus, registry=core.registry, consents=getattr(core, "consent", None),
                  archivist=getattr(core, "archivist", None))
        app.extensions["puffpai.zone3"] = z
    return z


__all__ = ["Zone3", "current", "for_app"]
