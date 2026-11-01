"""The Bridge service: one place every send asks (ZONES_TOGETHER section 2, Z3 section 2).

``Bridge.request(...)`` builds an ``egress.request`` check and puts it on the
Bus. Drawbridge answers it (``agent:drawbridge``) by running the rules-only
:func:`.policy.decide`. The Core then publishes ``egress.decision`` (Core
only) and writes one ledger line. No answer by the deadline, a crash, or an
answer that does not match the rules' own result is a deny (X26).

``Bridge.allowed(subject, host, labels)`` is the short form other lanes call.

The owner's choices arrive here only from owner routes and Yes records:
``open``/``close``, ``set_switch``, and grants made from a ``package``,
``host`` or ``cloud`` record (:func:`.policy.grants_from_record`). Narrowing
works even while Drawbridge is down: close, switch off, drop grants (X18).
One address and port for one package (D110, :mod:`.addresses`) is a ``host``
Yes in the ``run`` phase: an ordinary run grant, kept across a Core restart.
A cloud build's public read (D121) is one exact-host ``toolsmith:read`` grant per
host, made at send time from that build's plan Yes (:meth:`Bridge.open_read`).

**Build grants live only while Toolsmith works** (Z3 section 2, lane I3b). A Yes whose grants belong to a
build (:func:`build_of`: a ``cloud`` Yes, a ``host`` Yes in the ``build`` phase such as her wizard search
choice, a build's plan Yes ``toolsmith-build-*``) turns its switch on, but its grants are made only while
a build runs: :meth:`Bridge.open_build` (Toolsmith's process starts) makes them from her live records,
never turning a switch on; :meth:`Bridge.end_build` (it ended, any way) drops them. Install grants drop
when the install ends (:meth:`Bridge.end_install`). :meth:`Bridge.close_stale` (Core start and stop)
drops every build and install grant a crash or a hard kill left.
"""

from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import anchor as _anchor
from ..bus import Bus, Source
from ..consent import ConsentStore
from . import hosts, policy as rules
from .ledger import Ledger
from .policy import (READ_SUBJECT, SEARCH_HOSTS, WEB_KEY_HOSTS, Decision, GrantRefused, Policy, decide, default_share,
                     grants_from_record, reads_public_yes)
from .vault_anchor import CANT_CONFIRM_LINE, LEDGER, MISMATCH_LINE, VaultCheck

logger = logging.getLogger(__name__)
DRAWBRIDGE = Source("agent", "agent:drawbridge")
CORE = Source("core", "core:bridge")
DEADLINE_MS = 2000
DOWN = Decision("deny", "floor", "bridge_closed", ledger_code="drawbridge_down")
DOWN_LINE = ("Drawbridge stopped answering, so nothing new is added and nothing leaves your computer. "
             "Restarting it.")
TAMPER_LINE = ("Your Bridge settings were changed outside PuffPAI, so the Bridge is closed. "
               "Check and open it again.")


GRANT_REFUSED_LINE = ("Something you said Yes to names a place the Bridge never allows, so nothing from that "
                      "Yes was added. Check the card again.")


#: A Toolsmith build's name on its plan Yes and on the Bridge (``pkg:<this><6 hex>`` for its tries).
BUILD_TARGET = "toolsmith-build-"
#: Toolsmith's own subjects: reached only in the ``build`` phase.
TOOLSMITH_SUBJECTS = ("toolsmith:search", "toolsmith:cloud", READ_SUBJECT)

#: The posture in the owner's words (:func:`plain`): ``puffpai bridge status``, the Desktop and the wizard
#: say the same thing. Her wizard search choice opens the policy for Toolsmith's build-time search only, so
#: while that is all it allows, it reads as closed with that one exception.
SEARCH_SUBJECT = "toolsmith:search"
CLOSED_LINE = "The Bridge is closed. Nothing leaves your computer without your consent."
#: Each host Toolsmith's search may name, as she reads it. The Bridge says only the places her Yes names: a bot's
#: older search Yes names GitHub alone, and its line says GitHub alone.
PLACE_OF = {"api.github.com": "GitHub", "raw.githubusercontent.com": "GitHub", "github.com": "GitHub",
            "codeberg.org": "Codeberg", "registry.npmjs.org": "npm", "pypi.org": "PyPI",
            "registry.modelcontextprotocol.io": "the MCP server list", "data-v2.hacs.xyz": "HACS",
            "clawhub.ai": "ClawHub", "skills.sh": "skills.sh", "nousresearch.github.io": "the Hermes skills index",
            "huggingface.co": "Hugging Face", "opds.library.kiwix.org": "Kiwix", "catalog.data.gov": "Data.gov",
            "api.search.brave.com": "Brave Search", "api2.marginalia-search.com": "Marginalia Search"}
#: Said when it can't be told which places her Yes names (an older Core's posture).
SOME_PLACES = "the places you said Yes to"


def places_said(covered: Any) -> str:
    """The places these hosts are, in plain words ("GitHub, Codeberg and npm"); a host she added herself by its own
    name. None known: :data:`SOME_PLACES`."""
    got = {str(h) for h in covered or [] if h}
    names = list(dict.fromkeys(name for host, name in PLACE_OF.items() if host in got)) + sorted(got - set(PLACE_OF))
    if not names:
        return SOME_PLACES
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


#: The places a full search Yes names (the wizard's step 4, the Developer page's card). The same words as there.
SEARCH_PLACES = places_said(SEARCH_HOSTS)
_ONLY = "The Bridge is closed. While Toolsmith builds, it may search {places}; nothing else goes out."
#: A build runs and its search grant is live (its hosts are allowed right now): the honest word is open.
_BUILDING = "The Bridge is open while Toolsmith builds: it may search {places}; nothing else goes out."
SEARCH_ONLY_LINE = _ONLY.format(places=SEARCH_PLACES)
SEARCH_BUILDING_LINE = _BUILDING.format(places=SEARCH_PLACES)


CLOUD_SUBJECT = "toolsmith:cloud"
#: The one search she asks for by pressing Test on an API she added (``Ports.one_send_yes``). No package holds it.
OWNER_TEST_SUBJECT = "core:owner-test"
_PHASE_WORDS = {"run": "while it runs", "install": "while it installs", "build": "while Toolsmith builds"}


def who_line(subject: str, names: dict | None = None) -> str:
    """A Bridge subject in her words: a package by its name, Toolsmith's parts by what they do."""
    subject = str(subject or "")
    if subject == SEARCH_SUBJECT:
        return "Toolsmith's search for apps and knowledge"
    if subject == CLOUD_SUBJECT:
        return "Toolsmith's cloud model"
    if subject == READ_SUBJECT:
        return "Toolsmith reading public web pages, only to read"
    if subject == OWNER_TEST_SUBJECT:
        return "the one Test you pressed"
    if subject.startswith("pkg:" + BUILD_TARGET):
        return "the apps Toolsmith is trying"
    if subject.startswith("pkg:"):
        pid = subject[4:]
        return str((names or {}).get(pid) or pid)
    return subject


def _who(g: dict, names: dict | None) -> str:
    """One live grant in her words. A build's own grant in the ``build`` phase is the Core reading the
    knowledge on her plan (lane I11): its tries only ever get ``install`` grants."""
    subject = str(g.get("subject") or "")
    if subject.startswith("pkg:" + BUILD_TARGET) and g.get("phase") == "build" and g.get("from", "grant") == "grant":
        return "Toolsmith reading the knowledge on your plan"
    return who_line(subject, names)


def plain(view: dict, names: dict | None = None) -> dict:
    """``{"shown": "open" | "closed", "lines": [...]}`` for a :meth:`Bridge.view` (or ``GET /v1/bridge``).
    *names*: package id to its name (the Registry), so a package is said by its name, never ``pkg:<id>``.

    It reads "open" only when something can actually go out: a live grant, or a switch with a live Yes
    behind it whose hosts open while Toolsmith builds (its cloud model, the apps it tries). Toolsmith's
    build-time search alone reads closed, with its own line, until a build runs: then its search grant is
    live and it reads open, with :data:`SEARCH_BUILDING_LINE`. Those lines name only the places her Yes names
    (``search_hosts``, or the search grants' own hosts). Anything else reads closed. A view without ``live`` (an
    older Core's posture) is read from its grants and switches, never more open than that."""
    if "live" not in view:
        view = _view_of(view)
    if view.get("setting") != "open":
        return {"shown": "closed", "lines": [CLOSED_LINE]}
    live = [g for g in view.get("live") or [] if isinstance(g, dict)]
    out = [g for g in live if g.get("subject") != SEARCH_SUBJECT]
    named = view.get("search_hosts") or [g.get("host") for g in live if g.get("subject") == SEARCH_SUBJECT]
    places = places_said(named)
    if not out:
        if any(g.get("subject") == SEARCH_SUBJECT and g.get("from", "grant") == "grant" for g in live):
            return {"shown": "open", "lines": [_BUILDING.format(places=places)]}       # a build is searching now
        return {"shown": "closed", "lines": [_ONLY.format(places=places) if view.get("search") else CLOSED_LINE]}
    lines = ["The Bridge is open."]
    if view.get("search"):
        lines.append(f"While Toolsmith builds, it may search {places}.")
    if any(g.get("subject") == CLOUD_SUBJECT for g in out):
        lines.append("While Toolsmith builds, it may use the cloud model you picked.")
    who = sorted({f"{_who(g, names)} ({_PHASE_WORDS.get(g.get('phase'), 'for now')})"
                  for g in out if g.get("subject") != CLOUD_SUBJECT})
    if who:
        lines.append("Let through now: " + ", ".join(who) + ".")
    return {"shown": "open", "lines": lines}


def _view_of(posture: dict) -> dict:
    """An older posture (no ``live``): grants whose switch is on count as live; search is its switch."""
    on = {k for k, v in (posture.get("switches") or {}).items() if v}
    setting = "open" if posture.get("setting", posture.get("state")) == "open" and not posture.get("damaged") \
        else "closed"
    live = [g for g in posture.get("grants") or [] if isinstance(g, dict) and g.get("subject") in on] \
        if setting == "open" else []
    return {"setting": setting, "live": live, "search": setting == "open" and SEARCH_SUBJECT in on}


def _past(until: Any, now: datetime) -> bool:
    """A grant's ``until`` has passed (an unreadable one counts as passed, as in ``policy.decide``)."""
    if not until:
        return False
    try:
        return datetime.fromisoformat(str(until).replace("Z", "+00:00")) < now
    except ValueError:
        return True


def _package_switch(subject: str) -> bool:
    """A package's own switch (not a build's tries): it means nothing once no grant is behind it."""
    return subject.startswith("pkg:") and not subject.startswith("pkg:" + BUILD_TARGET)


def build_target(build_id: str) -> str:
    """The same short name as ``toolsmith_host.store.bridge_target`` (the build id's random part)."""
    return BUILD_TARGET + str(build_id).rsplit("-", 1)[-1]


def record_subject(rec: dict) -> str:
    """The Bridge subject a Yes record grants (as :func:`.policy.grants_from_record` names it)."""
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    kind, target = scope.get("kind"), str(scope.get("target") or "")
    return f"pkg:{target}" if kind == "package" and not target.startswith(("pkg:", "provider:")) else target


def build_of(rec: dict) -> str | None:
    """Whose build a Yes record's grants belong to: ``"*"`` for any build (a cloud Yes, a host Yes in the
    ``build`` phase, such as her search choice), a build's own name for its plan Yes, None for the rest."""
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    kind, target = scope.get("kind"), str(scope.get("target") or "")
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
    if kind == "cloud" or (kind == "host" and (covers.get("phase") == "build" or target in TOOLSMITH_SUBJECTS)):
        return "*"
    if kind == "package" and target.startswith(BUILD_TARGET):
        return target
    return None


def is_build_grant(grant: dict) -> bool:
    """A grant that only a running build may hold: the ``build`` phase, Toolsmith's own subjects, a build's tries."""
    subject = str(grant.get("subject") or "")
    return grant.get("phase") == "build" or subject in TOOLSMITH_SUBJECTS or subject.startswith("pkg:" + BUILD_TARGET)


def _has_hosts(rec: dict) -> bool:
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
    return any(covers.get(k) for k in ("hosts", "install_hosts", "subhosts", "lan", "knowledge_hosts"))


def is_key_yes(rec: dict) -> bool:
    """Her Yes for her own key on one web search host (``PUT /v1/toolsmith/search-key/<id>``), or for one API she added
    (``covers.owner_api``), not a search Yes."""
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
    named = covers.get("hosts") if isinstance(covers.get("hosts"), list) else []
    return record_subject(rec) == SEARCH_SUBJECT and len(named) == 1 \
        and (named[0] in WEB_KEY_HOSTS.values() or isinstance(covers.get("owner_api"), dict))


class OpenRefused(PermissionError):
    """Opening the Bridge was refused (safe mode without the right PIN). ``status`` (403 | 503), ``reasons``."""

    def __init__(self, status: int, line: str, reasons: list[str]) -> None:
        super().__init__(line)
        self.status, self.line, self.reasons = int(status), line, list(reasons)


class Bridge:
    def __init__(self, state: Path | str, bus: Bus | None = None, *, consents: ConsentStore | None = None,
                 anchor: _anchor.Anchor | None = None, share_of: Callable[[str], str] = default_share,
                 on_card: Callable[[dict], None] | None = None, vault: VaultCheck | None = None) -> None:
        self.state = Path(state)
        self.bus = bus or Bus()
        self.anchor = anchor or _anchor.for_state(self.state)
        self.vault = vault or VaultCheck(state=self.state, bus=self.bus, anchor=self.anchor)
        self.extra_logs: dict = {}                 # Zone3 adds the audit log, so the vault check covers it
        self.on_card = on_card
        self.cards: list[dict] = []
        self.consents = consents or ConsentStore(self.state / "consent.db", self.bus, anchor=self.anchor)
        self.policy = Policy(self.state / "settings" / "bridge" / "policy.json", anchor=self.anchor,
                             seal=self.vault.seal_policy, on_damaged=self._policy_damaged)
        self.share_of = share_of
        self.ledger = Ledger(self.state / "logs" / "outside.jsonl", on_tamper=self._ledger_tampered,
                             anchor=self.anchor, seal=self.vault.sealer(LEDGER))
        self._pending: dict[str, Decision] = {}
        self._lock = threading.Lock()
        self._builds: dict[str, int] = {}          # build id -> Toolsmith processes working on it now
        self._read_yes: dict[str, set[str]] = {}   # build id -> the plan Yes its public reads were granted from (D121)
        self.answering = False
        # ``open_guard(pin) -> None | (status, line, reasons)``: the Core's wiring sets it (safe mode: the PIN).
        self.open_guard: Callable[[Any], tuple | None] | None = None
        self.consents.on_granted(self._on_yes)
        self.start_drawbridge()
        if not self.anchor.available:
            self._card("no_machine_key", self.anchor.no_key_line())
        elif self.policy.damaged:
            self._card("bridge_damaged", TAMPER_LINE)

    # -- Drawbridge's answerer -------------------------------------------------
    def start_drawbridge(self) -> None:
        """Make Drawbridge the one answerer of ``egress.request`` (once per Bus)."""
        if self.answering:
            return
        try:
            self.bus.answer("egress.request", self._answer, DRAWBRIDGE)
            self.answering = True
        except ValueError:
            self.answering = False          # another answerer holds it: requests deny unless it is us

    def _answer(self, message: dict) -> dict:
        payload = message["payload"]
        d = decide(payload, self.policy, self.consents, self.share_of)
        with self._lock:
            self._pending[payload["req_id"]] = d
        return {"verdict": d.verdict, "reason_code": d.reason_code}

    # -- asking ---------------------------------------------------------------
    def request(self, subject: str, host: str, *, port: int | None = None, phase: str = "run",
                scheme: str = "https", method: str | None = None, labels: list[str] | None = None,
                top_tier: str | None = None, payload_sha: str | None = None, via: str = "core",
                log: bool = True) -> Decision:
        """Ask the Bridge. Publishes ``egress.decision`` and writes one ledger line."""
        req_id = "r_" + secrets.token_hex(8)
        h = hosts.clean(host)
        p = port if isinstance(port, int) else (80 if scheme == "http" else 443)
        carries: dict[str, Any] = {"labels": list(labels or [])}
        if top_tier:
            carries["top_tier"] = top_tier
        if payload_sha:
            carries["payload_sha"] = payload_sha
        payload: dict[str, Any] = {"req_id": req_id, "subject": subject, "phase": phase,
                                   "dest": {"host": h, "port": p, "scheme": scheme}, "carries": carries}
        if method:
            payload["method"] = method
        msg = Bus.message("egress.request", payload, CORE, deadline_ms=DEADLINE_MS)
        reply = self.bus.ask(msg)
        with self._lock:
            d = self._pending.pop(req_id, None)
        if d is None or reply.get("verdict") != d.verdict or reply.get("reason_code") != d.reason_code:
            # No check, a crashed or timed-out check, or a reply the rules did not give: no (X26).
            if reply.get("reason_code") == "rejected" and d is None:
                d = Decision("deny", "floor", "not_on_list")   # the request itself broke the contract
            else:
                d = DOWN
        ledger_id = None
        if log:
            ledger_id = self.ledger.record(subject, host=h, port=p, outcome="done" if d.allowed else "refused",
                                           code=d.ledger_code or d.reason_code, via=via, phase=phase,
                                           method=method or ("CONNECT" if scheme == "https" else "GET"),
                                           req_id=req_id, rule=d.rule)
        if ledger_id:
            # A short reference (the first 24 hex of the line hash): a bare 64-hex value reads as a
            # random token to the floor key check (D50) and the event would be dropped.
            ledger_id = "L" + ledger_id[:24]
        event = {"req_id": req_id, "verdict": d.verdict, "by": d.by, "reason_code": d.reason_code,
                 "rule": d.rule, "ledger_id": ledger_id}
        self.bus.publish(Bus.message("egress.decision", event, CORE))
        return d

    def allowed(self, subject: str, host: str, labels: list[str] | None = None, **kw: Any) -> Decision:
        """The short form: may *subject* send to *host* with these labels? (``zone3.bridge.allowed``)."""
        return self.request(subject, host, labels=labels, **kw)

    def peek(self, subject: str, host: str, phase: str = "build") -> bool:
        """Would *subject* reach *host* over https right now? The rules only (:func:`decide`): no Bus message, no
        line in the outside log, since nothing is sent. Can't tell: no."""
        try:
            return decide({"subject": subject, "phase": phase, "dest": {"host": host, "port": 443, "scheme": "https"},
                           "carries": {"labels": []}}, self.policy, self.consents, self.share_of).allowed
        except Exception:  # noqa: BLE001 - no decision means no
            return False

    # -- owner choices --------------------------------------------------------
    def posture(self) -> dict:
        data = self.policy.fresh()
        return {"state": "closed" if self.policy.damaged else data.state, "damaged": self.policy.damaged,
                "version": data.version, "sha": self.policy.sha(),
                "grants": [{k: g[k] for k in ("id", "subject", "host", "port", "subhosts", "phase", "lan")}
                           for g in data.grants],
                "switches": {s: bool(v.get("on")) for s, v in data.switches.items()},
                "blocks": [{k: b[k] for k in ("pattern", "kind", "source")} for b in data.blocks]}

    def view(self, names: dict | None = None) -> dict:
        """The Bridge as the owner sees it: ``GET /v1/bridge``, ``GET /v1/settings``, ``puffpai bridge status``,
        the Desktop and the wizard all read this, so they always agree. ``state`` and ``shown`` are the same
        word: "open" only when something can actually go out (:func:`plain`). ``setting`` is the policy's own
        open or closed (what ``open`` and ``close`` change). ``live``: each grant that can be used now (the
        setting open, its switch on, not past its time, its Yes still granted), plus each switch with a live
        Yes behind it whose hosts open only while Toolsmith builds. A damaged or missing policy, or no machine
        key, reads closed with nothing live."""
        posture = self.posture()
        data = self.policy.fresh()
        ok = posture["state"] == "open" and not self.policy.damaged and self.anchor.available
        setting = "open" if ok else "closed"
        switches = posture.get("switches") or {}
        live: list[dict] = []
        named: list[str] = []
        if ok:
            now = datetime.now(timezone.utc)
            for g in data.grants:
                if not switches.get(g.get("subject")) or _past(g.get("until"), now):
                    continue
                rec = self.consents.granted(g.get("consent"))
                if rec is None or rec.get("sha") != g.get("consent_sha"):
                    continue
                live.append({"subject": g["subject"], "phase": g.get("phase"), "host": g.get("host"),
                             "port": g.get("port"), "from": "grant"})
            with self._lock:
                running = {build_target(b) for b in self._builds}
            for rec in self.consents.live():
                subject = record_subject(rec)
                if rules.READ_PUBLIC_PAGES and reads_public_yes(rec) and build_of(rec) in running \
                        and switches.get(READ_SUBJECT, True) and not any(x["subject"] == READ_SUBJECT for x in live):
                    # A build runs under her Yes for public reads (D121): said from its start, before the first read.
                    live.append({"subject": READ_SUBJECT, "phase": "build", "from": "yes"})
                if build_of(rec) is None or not _has_hosts(rec) or not switches.get(subject):
                    continue
                if subject == SEARCH_SUBJECT:
                    named += [str(h) for h in (rec.get("covers") or {}).get("hosts") or []]
                if any(x["subject"] == subject for x in live):
                    continue
                live.append({"subject": subject, "phase": "build", "from": "yes"})
        search = any(x["subject"] == SEARCH_SUBJECT for x in live)
        said = plain({"setting": setting, "live": live, "search": search, "search_hosts": named}, names)
        return dict(posture, state=said["shown"], shown=said["shown"], setting=setting, search=search, live=live,
                    search_hosts=sorted(set(named)), said=said["lines"])

    def open(self, by: str = "owner", pin: str | None = None) -> dict:
        """Open the Bridge. The guard runs first (safe mode: the PIN, else :class:`OpenRefused`)."""
        if self.open_guard is not None:
            try:
                refused = self.open_guard(pin)
            except Exception:  # noqa: BLE001 - a guard that breaks is a no (X26)
                refused = (403, "BuildBot could not check whether the Bridge may open, so it stays closed.", [])
            if refused:
                raise OpenRefused(*refused)
        if self.vault.mismatch:
            # She checked after a "doesn't match your vault" card and opens again: what is here now is expected.
            self.vault.accept(self.policy, dict({LEDGER: self.ledger.chain}, **self.extra_logs), self.consents)
        try:
            self.policy.set_state("open")
        except _anchor.AnchorUnavailable:
            self._card("no_machine_key", _anchor.NO_KEY_LINE)
            return self.posture()
        self._posture("open", by)
        return self.posture()

    def close(self, by: str = "owner") -> dict:
        try:
            self.policy.set_state("closed")
        except _anchor.AnchorUnavailable:
            pass                                # no machine key: every decision is closed already
        self._posture("closed", by)
        return self.posture()

    def set_switch(self, subject: str, on: bool) -> None:
        self.policy.set_switch(subject, on)

    def end_install(self, package_id: str) -> int:
        """Install grants close when install ends (02 field reference). Its switch goes too when no grant is
        left behind it (an app whose card had only setup hosts), so the Bridge never reads as open for it."""
        subject = f"pkg:{package_id}"
        try:
            return self.policy.drop_where(lambda g: g.get("subject") == subject and g.get("phase") == "install",
                                          forget_empty=lambda s: s == subject)
        except _anchor.AnchorUnavailable:
            return 0                            # no machine key: every decision is closed already

    # -- build grants: they live only while Toolsmith works on a build (Z3 section 2) ------------
    def builds_running(self) -> list[str]:
        with self._lock:
            return sorted(self._builds)

    def open_build(self, build_id: str, *, plan_consent: str | None = None) -> int:
        """Toolsmith's process for *build_id* starts (its plan or its build). Its build grants open from her
        live Yes records: Toolsmith's own build-phase subjects (her search choice, a cloud Yes) and this
        build's plan Yes (its tries' install hosts). Only for a subject whose switch she left on: this
        never turns a switch on (OFF beats any grant). Returns how many grants it added."""
        target = build_target(build_id)
        with self._lock:
            self._builds[build_id] = self._builds.get(build_id, 0) + 1
        added = 0
        try:
            switches = self.policy.fresh().switches
            have = {g.get("consent") for g in self.policy.fresh().grants}
            for rec in self.consents.live():
                which = build_of(rec)
                if which is None or rec["consent_id"] in have or not _has_hosts(rec):
                    continue
                if which != "*" and (which != target or (plan_consent and rec["consent_id"] != plan_consent)):
                    continue
                if not (switches.get(record_subject(rec)) or {}).get("on"):
                    continue
                added += len(grants_from_record(self.policy, rec))
        except (GrantRefused, ValueError, _anchor.AnchorUnavailable):
            self._card("grant_refused", GRANT_REFUSED_LINE)
        if added:
            self._posture(self.policy.fresh().state, "core")
        return added

    def open_read(self, build_id: str, host: str, rec: dict) -> bool:
        """One public host for this cloud build's next read (D121): one exact-host grant from its plan Yes *rec*, kept
        until the build ends. Only while that build runs, the Bridge is open and her Off is not on it. It never turns a
        switch she left off back on, and it says nothing to packages. :func:`.policy.decide` still decides each send
        and writes its line: this only names the host. True when the grant is there now."""
        if not rules.READ_PUBLIC_PAGES or not reads_public_yes(rec) or build_of(rec) != build_target(build_id):
            return False
        cid = str(rec.get("consent_id") or "")
        h = hosts.clean(host)
        with self._lock:
            if build_id not in self._builds:
                return False
        try:
            data = self.policy.fresh()
            off = (data.switches.get(READ_SUBJECT) or {"on": True}).get("on") is not True
            if self.policy.damaged or data.state != "open" or off:
                return False
            if not any(g["subject"] == READ_SUBJECT and g["host"] == h and g["consent"] == cid for g in data.grants):
                self.policy.add_grant(READ_SUBJECT, h, 443, phase="build", consent=rec, schemes=["https"])
            with self._lock:
                alive = build_id in self._builds
                if alive:
                    self._read_yes.setdefault(build_id, set()).add(cid)
            if not alive:                       # it ended while the grant was made: nothing stays behind
                self.policy.drop_where(lambda g: g["subject"] == READ_SUBJECT and g["consent"] == cid)
            return alive
        except (GrantRefused, ValueError, _anchor.AnchorUnavailable):
            return False

    def end_build(self, build_id: str) -> int:
        """Toolsmith's process for *build_id* ended, whatever way (done, not yet, gave up, an error, a stop).
        This build's grants close (its switch goes too), its public reads with them; when no other build runs,
        every build-phase grant closes. Her Yes records stay: the next build opens from them again."""
        target = build_target(build_id)
        with self._lock:
            left = self._builds.get(build_id, 0) - 1
            if left > 0:
                self._builds[build_id] = left
                reads: set[str] = set()
            else:
                self._builds.pop(build_id, None)
                reads = self._read_yes.pop(build_id, set())
            others = bool(self._builds)
            alive = {build_target(b) for b in self._builds}
        mine = f"pkg:{target}"

        def match(g: dict) -> bool:
            if g["subject"] == mine and target not in alive:
                return True
            if g["subject"] == READ_SUBJECT and g.get("consent") in reads:
                return True
            return not others and is_build_grant(g)
        try:
            return self.policy.drop_where(match, forget=lambda s: s == mine and target not in alive,
                                          forget_empty=lambda s: s == READ_SUBJECT)
        except _anchor.AnchorUnavailable:
            return 0                            # no machine key: every decision is closed already

    def close_knowledge(self, build_id: str) -> int:
        """This build's knowledge grants close (lane I11): the Core's own reads, its subject in the ``build``
        phase, whichever host (the fixed list or a Data.gov file host). A build's tries only ever hold ``install``
        grants. Called after :meth:`end_build` whatever happened there, so a failed clean-up never leaves them
        open until the next Core start. When the grants can't be dropped, the build's switch goes off (off
        beats any grant). Returns how many grants it dropped."""
        mine = f"pkg:{build_target(build_id)}"
        try:
            return self.policy.drop_where(lambda g: g.get("subject") == mine and g.get("phase") == "build")
        except _anchor.AnchorUnavailable:
            return 0                            # no machine key: every decision is closed already
        except Exception:  # noqa: BLE001 - the switch still closes them
            logger.exception("knowledge grants of %s not dropped; its switch goes off", build_id)
            self.policy.set_switch(mine, False)
            return 0

    def close_stale(self) -> int:
        """At Core start (and stop): no build and no install runs, so every build grant and every install
        grant left from before (a crash, a hard kill) closes. Returns how many."""
        with self._lock:
            self._builds.clear()
            self._read_yes.clear()
        try:
            return self.policy.drop_where(lambda g: is_build_grant(g) or g.get("phase") == "install",
                                          forget=lambda s: s.startswith("pkg:" + BUILD_TARGET),
                                          forget_empty=lambda s: _package_switch(s) or s == READ_SUBJECT)
        except _anchor.AnchorUnavailable:
            return 0

    def forget(self, subject: str) -> int:
        return self.policy.drop_grants(subject)

    def drop_consent(self, consent_id: str, subject: str) -> int:
        """The grants of one Yes go (her address removed, D110), and the subject's switch too when it is on with no
        grant left behind it. Her own Off stays. Only narrows; a revoked Yes already stops :func:`decide`, so with no
        machine key (every decision is closed already) nothing is lost."""
        try:
            return self.policy.drop_where(lambda g: g.get("consent") == consent_id, forget_empty=lambda s: s == subject)
        except _anchor.AnchorUnavailable:
            return 0

    def package_card(self, manifest: dict, *, subhosts: list[str] | None = None,
                     floor: bool = False) -> dict:
        """Raise the Bridge part of a package's Yes card from its manifest (lane L1 calls this).
        Hosts that break the floor are refused here, before the owner sees anything."""
        from .policy import check_hosts
        needs = manifest.get("needs") or {}
        run_hosts = [h for h in needs.get("hosts") or [] if h not in (subhosts or [])]
        problems = check_hosts(run_hosts) + check_hosts(needs.get("install_hosts") or []) \
            + check_hosts(subhosts or [], subhosts=True)
        if problems:
            raise ValueError(" ".join(problems))
        covers = {"hosts": run_hosts, "install_hosts": list(needs.get("install_hosts") or []),
                  "subhosts": list(subhosts or [])}
        return self.consents.ask("package", manifest["id"], covers, floor=floor)

    def _on_yes(self, rec: dict) -> None:
        if rec["scope"]["kind"] not in ("package", "host", "cloud"):
            return
        which = build_of(rec)
        if which is not None:
            # A build grant lives only while Toolsmith works (Z3 section 2). Her Yes turns the switch on as
            # always; the grants open when a build runs (now, if one does: open_build reads this record).
            if not _has_hosts(rec):
                return
            with self._lock:
                running = {build_target(b) for b in self._builds}
            try:
                # Her Yes for one key names that one host: it never turns Toolsmith's search switch on, or a search
                # Yes she turned off would wake with it. With the search off, the key waits (off beats any grant), and
                # adds no grant now either: adding one turns its subject's switch on.
                key_waits = is_key_yes(rec) and not (self.policy.fresh().switches.get(record_subject(rec)) or {}).get("on")
                if not is_key_yes(rec):
                    self.policy.set_switch(record_subject(rec), True)
                if key_waits or not running or (which != "*" and which not in running):
                    self._posture(self.policy.fresh().state, "owner")
                    return
            except (GrantRefused, ValueError, _anchor.AnchorUnavailable):
                self._card("grant_refused", GRANT_REFUSED_LINE, consent_id=rec.get("consent_id"))
                return
        try:
            grants_from_record(self.policy, rec)
        except (GrantRefused, ValueError, _anchor.AnchorUnavailable):
            self._card("grant_refused", GRANT_REFUSED_LINE, consent_id=rec.get("consent_id"))
            return
        covers = rec.get("covers") or {}
        # bridge.posture reaches every package: a place she allowed for one package is not said to the others (D110).
        self._posture(self.policy.fresh().state, "owner",
                      [] if isinstance(covers.get("owner_address"), dict) else [g for g in covers.get("hosts") or []])

    # -- cards and posture ------------------------------------------------------
    def _posture(self, state: str, by: str, changed: list[str] | None = None) -> None:
        payload: dict[str, Any] = {"state": state, "by": by}
        clean = [hosts.split_hostport(h)[0] for h in (changed or []) if h]
        if clean:
            payload["hosts_changed"] = clean
        self.bus.publish(Bus.message("bridge.posture", payload, CORE))

    def _card(self, kind: str, line: str, **extra: Any) -> dict:
        card = {"kind": kind, "line": line, **extra}
        self.cards.append(card)
        if self.on_card is not None:
            try:
                self.on_card(card)
            except Exception:  # noqa: BLE001
                pass
        return card

    def vault_unlocked(self, logs: dict | None = None) -> list[dict]:
        """D54: compare the vault's copy with the policy and the logs; a mismatch closes the Bridge with a card."""
        try:
            problems = self.vault.unlocked(self.policy, dict({LEDGER: self.ledger.chain},
                                                             **dict(self.extra_logs, **(logs or {}))), self.consents)
        except Exception:  # noqa: BLE001 - a check that breaks closes the Bridge, with a card, never passes
            logger.exception("the unlock check failed")
            problems = [{"what": "check", "why": "cant_confirm"}]
        self._vault_problems(problems)
        return problems

    def vault_at_start(self, has_run: bool) -> list[dict]:
        """At Core start: the files against the pending values kept on disk (09 section 3)."""
        try:
            problems = self.vault.at_start(self.policy, dict({LEDGER: self.ledger.chain}, **self.extra_logs),
                                           self.consents, has_run=has_run)
        except Exception:  # noqa: BLE001
            logger.exception("the start check failed")
            problems = [{"what": "check", "why": "cant_confirm"}]
        if problems:
            self.vault.mismatch = True
        self._vault_problems(problems)
        return problems

    def _vault_problems(self, problems: list[dict]) -> None:
        if problems:
            try:
                self.policy.set_state("closed")
            except Exception:  # noqa: BLE001 - damaged or no key: it reads closed already
                pass
            self._posture("closed", "core")
            cant = all(p["why"] in ("cant_confirm", "archivist_failed") for p in problems)
            self._card("vault_mismatch", CANT_CONFIRM_LINE if cant else MISMATCH_LINE,
                       what=[p["what"] for p in problems], why=sorted({p["why"] for p in problems}))

    def _policy_damaged(self) -> None:
        self._card("bridge_damaged", TAMPER_LINE)

    def _ledger_tampered(self, note: dict) -> None:
        if note.get("why") == "no_machine_key":
            return                              # said once at start (no_machine_key card); closed already
        # An edit or a cut of the outside log closes the Bridge (X14, X24): detect, then fail closed.
        try:
            self.policy.set_state("closed")
        except Exception:  # noqa: BLE001
            pass
        self._card("ledger_damaged", "The record of what left your computer was changed outside PuffPAI, "
                   "so the Bridge is closed. Check and open it again.")


__all__ = ["BUILD_TARGET", "Bridge", "CLOSED_LINE", "DOWN_LINE", "OWNER_TEST_SUBJECT", "PLACE_OF", "READ_SUBJECT", "SEARCH_BUILDING_LINE", "SEARCH_ONLY_LINE",
           "SEARCH_PLACES", "TAMPER_LINE", "build_of",
           "build_target", "is_build_grant", "is_key_yes", "places_said", "plain", "record_subject", "who_line"]
