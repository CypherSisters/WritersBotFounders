# Harvested in idea from PuffPAIBuildBot@63c8a037:src/puffpai/security/egress_doors.py (egress_allowed :775, _switch :713) and security/outside_switches.py (set_switch :513); rewritten as one pure decide()
"""The Bridge policy (Z3 section 2): the one decision point, owned by the Core.

Data (``settings\\bridge\\policy.json``, written only by the Core):

    state     closed | open          ships closed; open means "only what was granted"
    grants    [{id, subject, host, port, schemes, subhosts, phase, lan, consent, consent_sha, at, until}]
    switches  {subject: {on, at}}    one-tap off; off beats any grant; missing = off
    blocks    [{pattern, kind: host|suffix, source, reason, at}]
    exempt    [{pattern, source, consent, at}]   the owner's "Allow anyway" for one block
    version, sha, mac                every change bumps both; ``mac`` is under the machine key

The only inputs that can grant are the owner's Yes records (:mod:`..consent`).
Providers and feeds only add blocks. A missing, damaged or edited file reads
as closed (F1, X24), and the decision says ``bridge_closed``.

**Tamper anchor (09 section 3).** The file carries the sha of its body and an
HMAC of that sha under the machine key (:mod:`..anchor`). Both are checked at
start and at every reload (the file is re-read whenever its size or time
moves). A same-user script that adds a grant and recomputes the plain sha
fails the MAC: the policy reads damaged, the Bridge is closed, and a card says
so (X24). Honest limit: a program written to target PuffPAI can unwrap the
machine key (the machine wrap unwraps for any program running as her) and forge the MAC;
only the vault copy sealed at unlock (lane L3) stops that one.

**Monotonic version (D54).** ``version`` only ever goes up. The highest
version this bot has written is kept beside the file in ``policy.version``
(MAC'd); a policy older than it, or a deleted policy on a bot that has written
one, reads damaged (closed). Each save writes ``max(file, high) + 1``, and the
new ``(version, sha)`` goes to ``seal`` (the vault copy while it is open,
:mod:`.vault_anchor`), so a rollback of both files together is caught at the
next unlock.

**Schemes.** A grant names the schemes it allows: a grant on port 443 allows
only CONNECT (TLS), on port 80 only plain HTTP, on any other port both. So a
443 grant never allows plain HTTP to 443. A Core card may pin one scheme by
writing ``http://host:port`` or ``https://host:port`` in what it covers.

**One address for one package (D110).** A Yes with ``covers.owner_address`` carries exactly one ``host:port``: in
``lan`` for a home-network address or ``.local``/``.lan`` name, in ``hosts`` for a public name. :func:`owner_address_ok`
refuses any other shape. Its grant never wakes a subject she turned off.

**One public page for a cloud build (D121).** ``toolsmith:read`` is the Core's own subject, for its own read of a public
page. Its grants still name one exact host each, made one at a time from a build's plan Yes that holds
``covers.reads_public`` (:func:`reads_public_yes`, ``Bridge.open_read``), and only :meth:`Policy.add_grant` makes them.
Nothing here changes :func:`decide`, and no package or try can hold one.

:func:`decide` is a pure function of (request, policy, consents, label effects):
no network, no model, no free text. Drawbridge runs it (09 section 3).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import anchor as _anchor
from ..anchor import sha
from ..consent import ConsentStore
from . import hosts

PHASES = ("run", "install", "build")
SHARES = ("allow", "ask_always", "ask_each", "never")
SECRET_LABELS = ("secret.", "auth.password")
#: Agent subjects never get a grant (X28): the seven agents have no Bridge subject.
NO_GRANT_PREFIXES = ("agent:",)
#: The only Yes records whose ``knowledge_hosts`` grant anything: a Toolsmith build plan's (``bridge.BUILD_TARGET``).
KNOWLEDGE_TARGET = "toolsmith-build-"
#: The only hosts a build plan's ``knowledge_hosts`` may ever grant (lane I11), whatever the record lists, besides
#: a Data.gov file host (:func:`knowledge_host_ok`). The Core's knowledge fetch (``toolsmith_host.knowledge``)
#: reads this same list. A row in ``sources.json`` naming another host is never reached.
KNOWLEDGE_HOSTS = ("en.wikipedia.org", "www.gutenberg.org", "www.appropedia.org", "www.ifixit.com",
                   "commons.wikimedia.org", "thumb.wikimedia.org", "upload.wikimedia.org",
                   "lb.download.kiwix.org", "mirror.download.kiwix.org", "ftp.nluug.nl", "mirrors.dotsrc.org")
#: Every host Toolsmith's search Yes may name (the wizard's step 4, ``wiring.WizardBridge``). A fixed code list:
#: a row in ``sources.json`` naming another host is never reached (``toolsmith_host.address``).
SEARCH_HOSTS = ("api.github.com", "raw.githubusercontent.com", "github.com", "codeberg.org", "registry.npmjs.org",
                "pypi.org", "registry.modelcontextprotocol.io", "data-v2.hacs.xyz", "clawhub.ai", "skills.sh",
                "nousresearch.github.io", "huggingface.co", "opds.library.kiwix.org", "catalog.data.gov")
#: The ``web`` rows of ``sources.json`` this code knows, and their one host each. Never on the search Yes: each has
#: its own Yes card, raised when she links her own key, and needs that key (vault ``toolsmith.<id>``).
WEB_KEY_HOSTS = {"brave": "api.search.brave.com", "marginalia": "api2.marginalia-search.com"}
_GOV_HOST = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.gov")
#: Off: reads go only to the search places, and that part of the plan card's sentence goes away (D121).
READ_PUBLIC_PAGES = True
#: Off: found apps are not tried, and that part of the plan card's sentence goes away (D121).
TRY_FOUND_APPS = True
#: The Core's own read of one public page for a cloud build. No package, try or other Yes can hold it (D121).
READ_SUBJECT = "toolsmith:read"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _replace(tmp: Path, path: Path) -> None:
    """``os.replace``, a few times: on Windows another program can hold a new file for a moment. A save cut short here
    leaves the file ahead of its seal, and that reads as a rollback at the next unlock (D151)."""
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    os.replace(tmp, path)


def _plan_yes_holds(rec: Any, field: str) -> bool:
    scope = rec.get("scope") if isinstance(rec, dict) and isinstance(rec.get("scope"), dict) else {}
    covers = rec.get("covers") if isinstance(rec, dict) and isinstance(rec.get("covers"), dict) else {}
    return scope.get("kind") == "package" and str(scope.get("target") or "").startswith(KNOWLEDGE_TARGET) \
        and covers.get(field) is True


def reads_public_yes(rec: Any) -> bool:
    """Is *rec* a Toolsmith build's plan Yes that holds ``covers.reads_public`` (D121)? The only record ``toolsmith:read``
    can come from."""
    return _plan_yes_holds(rec, "reads_public")


def tries_found_yes(rec: Any) -> bool:
    """Is *rec* a Toolsmith build's plan Yes that holds ``covers.tries_found`` (D121): its card said "other apps"?"""
    return _plan_yes_holds(rec, "tries_found")


def is_secret_label(label: str) -> bool:
    return label.startswith(SECRET_LABELS[0]) or label == SECRET_LABELS[1]


def default_share(label: str) -> str:
    """Until Zone 1's label packs are wired (lane L3): secrets never leave, every other
    label asks each time (the strict default, D31(a))."""
    return "never" if is_secret_label(label) else "ask_each"


@dataclass(frozen=True)
class Decision:
    verdict: str                 # allow | deny | ask
    by: str                      # floor | policy | owner
    reason_code: str             # egress.decision reason_code
    rule: str | None = None      # grant id or block source
    ledger_code: str = ""        # finer code for the ledger (policy_damaged, ...)

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "by": self.by, "reason_code": self.reason_code, "rule": self.rule}


class GrantRefused(ValueError):
    """A grant the floor never allows (an agent subject, an address, a wildcard or public suffix)."""


@dataclass
class PolicyData:
    state: str = "closed"
    grants: list[dict] = field(default_factory=list)
    switches: dict[str, dict] = field(default_factory=dict)
    blocks: list[dict] = field(default_factory=list)
    exempt: list[dict] = field(default_factory=list)
    version: int = 0

    def body(self) -> dict:
        return {"state": self.state, "grants": self.grants, "switches": self.switches, "blocks": self.blocks,
                "exempt": self.exempt, "version": self.version}

    @classmethod
    def from_body(cls, body: dict) -> "PolicyData":
        if not isinstance(body, dict) or body.get("state") not in ("open", "closed"):
            raise ValueError("state")
        for key, typ in (("grants", list), ("switches", dict), ("blocks", list), ("exempt", list)):
            if not isinstance(body.get(key), typ):
                raise ValueError(key)
        return cls(state=body["state"], grants=list(body["grants"]), switches=dict(body["switches"]),
                   blocks=list(body["blocks"]), exempt=list(body["exempt"]), version=int(body.get("version", 0)))


class Policy:
    """The policy file and its in-memory copy. One writer: the Core."""

    def __init__(self, path: Path | str, *, anchor: _anchor.Anchor | None = None,
                 seal: Callable[[int, str], None] | None = None,
                 on_damaged: Callable[[], None] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # <state>/settings/bridge/policy.json -> the bot folder's anchor
        self.anchor = anchor or _anchor.for_state(self.path.parent.parent.parent)
        self.seal = seal
        self.on_damaged = on_damaged
        self._lock = threading.RLock()
        self.write_lock = self._lock           # D151: held across a change and its seal; the unlock check reads under it
        self.damaged = False
        self._good: PolicyData | None = None
        self._stat: tuple | None = None
        self.high_path = self.path.with_name(self.path.stem + ".version")
        self.high = 0
        self.data = self._load()

    @property
    def version(self) -> int:
        """The current version: the file's, never lower than the highest this bot wrote."""
        return max(self.fresh().version, self.high)

    def _read_high(self) -> int | None:
        """The highest version written (0 when never written), or None when its file was edited."""
        try:
            raw = json.loads(self.high_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, ValueError):
            return None
        v = raw.get("version") if isinstance(raw, dict) else None
        if not isinstance(v, int) or isinstance(v, bool) or v < 0 \
                or not self.anchor.check("policy_version", str(v), raw.get("mac")):
            return None
        return v

    def _write_high(self, v: int) -> None:
        text = json.dumps({"version": v, "mac": self.anchor.mac("policy_version", str(v))})
        tmp = self.high_path.with_name(self.high_path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        _replace(tmp, self.high_path)

    # -- file ---------------------------------------------------------------
    def _file_stat(self) -> tuple | None:
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            return None

    def _load(self) -> PolicyData:
        self._stat = self._file_stat()
        high = self._read_high()
        if high is None:
            self.damaged = True                # the version mark was edited outside PuffPAI
            return PolicyData()
        self.high = max(self.high, high)
        if self._stat is None:
            self.damaged = self.high > 0       # deleted, on a bot that has written one: closed, with a card
            return PolicyData()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            body = raw["body"]
            if raw.get("sha") != sha(body):
                raise ValueError("sha")
            if not self.anchor.check("policy", raw["sha"], raw.get("mac")):
                raise ValueError("mac")        # edited outside PuffPAI (or no machine key): closed
            data = PolicyData.from_body(body)
            if data.version < self.high:
                raise ValueError("older than the highest version written")   # a local rollback
        except (OSError, ValueError, KeyError, TypeError):
            self.damaged = True
            return PolicyData()            # closed, no grants
        self.damaged = False
        self._good = data
        return data

    def fresh(self) -> PolicyData:
        """The current data; re-read (and re-checked) if the file changed on disk."""
        if self._reread():
            self.say_damaged()
        return self.data

    def _reread(self) -> bool:
        """Re-read if the file changed on disk. True when that is what made it read damaged."""
        with self._lock:
            if self._file_stat() == self._stat:
                return False
            was = self.damaged
            self.data = self._load()
            return self.damaged and not was

    def say_damaged(self) -> None:
        if self.on_damaged is not None:
            try:
                self.on_damaged()              # the card, at runtime (X24)
            except Exception:  # noqa: BLE001
                pass

    def read_quietly(self) -> tuple[int, str, bool]:
        """``(version, sha, turned damaged)``, as :attr:`version` and :meth:`sha` say, telling nobody. For the unlock check
        (D151): it holds every writer's lock, and the card can start safe mode, which writes the audit log. So it calls
        :meth:`say_damaged` itself after letting them go."""
        with self._lock:
            turned = self._reread()
            return max(self.data.version, self.high), sha(self.data.body()), turned

    def sha(self) -> str:
        return sha(self.fresh().body())

    def _save(self) -> None:
        new_version = max(self.data.version, self.high) + 1
        body_sha = sha(dict(self.data.body(), version=new_version))
        try:
            mac = self.anchor.mac("policy", body_sha)
        except _anchor.AnchorUnavailable:
            self.data = self._load()               # nothing written: memory goes back to the file
            raise
        if self.damaged and self.path.exists():
            # Any write after an outside edit keeps the damaged file aside first, never overwrites it.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            try:
                os.replace(self.path, self.path.with_name(f"policy.damaged-{stamp}.json"))
            except OSError:
                pass
        self.data.version = new_version
        body = self.data.body()
        text = json.dumps({"body": body, "sha": body_sha, "mac": mac}, sort_keys=True, indent=1)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        _replace(tmp, self.path)
        self._write_high(new_version)          # after the file: a crash between reads as "file ahead", accepted
        self.high = new_version
        self._stat = self._file_stat()
        self.damaged = False
        self._good = self.data
        if self.seal is not None:
            try:
                self.seal(self.data.version, sha(body))
            except Exception:  # noqa: BLE001 - sealing is evidence; the file is already written
                pass

    # -- owner changes (called only from owner routes and Yes records) --------
    def set_state(self, state: str) -> None:
        if state not in ("open", "closed"):
            raise ValueError(state)
        with self._lock:
            self.fresh()
            if self.damaged and state == "closed":
                return                             # it reads closed already; the damaged file stays as evidence
            if self.damaged and state == "open":
                # The owner opens again after an outside edit: her last good copy if this
                # run had one, else a fresh empty policy (each app asks again). _save keeps
                # the damaged file aside, never deleted.
                self.data = PolicyData.from_body(self._good.body()) if self._good else PolicyData()
            self.data.state = state
            self._save()

    def set_switch(self, subject: str, on: bool) -> None:
        with self._lock:
            self.fresh()
            if on and subject.startswith(NO_GRANT_PREFIXES):
                raise GrantRefused("Agents never reach outside this computer.")
            self.data.switches[subject] = {"on": bool(on), "at": _now()}
            self._save()

    def add_grant(self, subject: str, host: str, port: int, *, phase: str, consent: dict,
                  subhosts: bool = False, lan: bool = False, until: str | None = None,
                  schemes: Iterable[str] | None = None) -> dict:
        """One grant from one live Yes record. The caller passes the record itself."""
        if subject.startswith(NO_GRANT_PREFIXES):
            raise GrantRefused("Agents never reach outside this computer.")
        if subject == "toolsmith:cloud" and phase != "build":
            raise GrantRefused("Toolsmith's cloud model is reached only while it builds.")
        if subject == READ_SUBJECT and not (phase == "build" and port == 443 and list(schemes or []) == ["https"]
                                            and not subhosts and not lan and reads_public_yes(consent)):
            raise GrantRefused("Toolsmith reads a public page only under a build's plan Yes, one https host at a time.")
        if phase not in PHASES:
            raise GrantRefused("unknown phase")
        if not isinstance(consent, dict) or not consent.get("consent_id") or not consent.get("sha"):
            raise GrantRefused("A grant needs the owner's Yes.")
        h = hosts.clean(host)
        if "*" in str(host):
            raise GrantRefused("A grant never uses a wildcard.")
        if lan:
            if subhosts or not hosts.is_lan_host(h):
                raise GrantRefused("A home-network grant is one exact private address, or a name ending in .local or .lan.")
        elif subhosts:
            if not hosts.subhost_grant_ok(h):
                raise GrantRefused(f"{h} is shared by many sites, so it can't be granted with all its sub-hosts.")
        elif not hosts.is_dns_name(h):
            raise GrantRefused(f"{host!r} is not a plain internet name.")
        if not isinstance(port, int) or not 0 < port < 65536:
            raise GrantRefused("bad port")
        sch = sorted(set(schemes)) if schemes else list(hosts.default_schemes(port))
        if not sch or any(s not in hosts.SCHEMES for s in sch):
            raise GrantRefused("A grant allows https, http or both.")
        grant = {"id": "g_" + secrets.token_hex(8), "subject": subject, "host": h, "port": port,
                 "schemes": sch, "subhosts": bool(subhosts), "phase": phase, "lan": bool(lan),
                 "consent": consent["consent_id"], "consent_sha": consent["sha"], "at": _now(), "until": until}
        with self._lock:
            self.fresh()
            self.data.grants.append(grant)
            # Her Yes for one address never wakes a package she turned off: only a subject with no switch yet goes on.
            # The same goes for a public read (D121): her Off on it stays off.
            covered = consent.get("covers") if isinstance(consent.get("covers"), dict) else {}
            if not ((isinstance(covered.get("owner_address"), dict) or subject == READ_SUBJECT)
                    and subject in self.data.switches):
                self.data.switches[subject] = {"on": True, "at": _now()}
            self._save()
        return dict(grant)

    def drop_grants(self, subject: str, phase: str | None = None) -> int:
        with self._lock:
            self.fresh()
            keep = [g for g in self.data.grants if not (g["subject"] == subject and phase in (None, g["phase"]))]
            n = len(self.data.grants) - len(keep)
            self.data.grants = keep
            if phase is None:
                self.data.switches.pop(subject, None)
            if n or phase is None:
                self._save()
            return n

    def drop_where(self, match: Callable[[dict], bool], forget: Callable[[str], bool] | None = None,
                   forget_empty: Callable[[str], bool] | None = None) -> int:
        """Drop every grant *match* says, in one save; the switches of subjects *forget* says go too, and so
        does every switch that is on, that *forget_empty* picks and that no grant is left behind (a package's
        switch after its install grants closed). Only narrows (X18): it never adds a grant or turns a switch on."""
        with self._lock:
            self.fresh()
            keep = [g for g in self.data.grants if not match(g)]
            n = len(self.data.grants) - len(keep)
            left = {g.get("subject") for g in keep}
            gone = [s for s, v in self.data.switches.items()
                    if (forget is not None and forget(s))
                    or (forget_empty is not None and (v or {}).get("on") and s not in left and forget_empty(s))]
            if not n and not gone:
                return 0
            self.data.grants = keep
            for s in gone:
                self.data.switches.pop(s, None)
            self._save()
            return n

    def add_block(self, pattern: str, kind: str, source: str, reason: str = "") -> bool:
        """Blocks only narrow. Never this computer, loopback or the Core's own names (X18)."""
        p = hosts.clean(pattern)
        if kind not in ("host", "suffix") or not p:
            return False
        if p in ("localhost",) or p.endswith(".localhost") or hosts.is_ip_literal(p):
            return False                   # X18: a feed listing this PC or an address is dropped
        with self._lock:
            self.fresh()
            self.data.blocks.append({"pattern": p, "kind": kind, "source": source, "reason": reason[:40],
                                     "at": _now()})
            self._save()
        return True

    def add_exempt(self, pattern: str, source: str, consent: dict) -> None:
        with self._lock:
            self.fresh()
            self.data.exempt.append({"pattern": hosts.clean(pattern), "source": source,
                                     "consent": consent["consent_id"], "at": _now()})
            self._save()

    def blocked(self, host: str) -> bool:
        """Does a block stop *host* now (the same check as :func:`decide`, with her "Allow anyway")?"""
        return _blocked(hosts.clean(host), self.fresh()) is not None

    def snapshot(self) -> dict:
        """What enforcers receive (Z3 4.1): allow sets and blocks, never labels or consents."""
        d = self.fresh()
        subjects: dict[str, dict] = {}
        if d.state == "open" and not self.damaged:
            for g in d.grants:
                if not d.switches.get(g["subject"], {}).get("on"):
                    continue
                s = subjects.setdefault(g["subject"], {"allow": [], "lan": []})
                entry = {"host": g["host"], "port": g["port"], "schemes": _schemes(g), "subhosts": g["subhosts"],
                         "phase": g["phase"]}
                s["lan" if g["lan"] else "allow"].append(entry)
        body = {"version": d.version, "subjects": subjects,
                "blocks": [{"pattern": b["pattern"], "kind": b["kind"]} for b in d.blocks]}
        return dict(body, sha=sha(body))


def _schemes(grant: dict) -> list[str]:
    got = grant.get("schemes")
    if isinstance(got, list) and got and all(s in hosts.SCHEMES for s in got):
        return list(got)
    return list(hosts.default_schemes(int(grant.get("port") or 0)))


def _blocked(host: str, data: PolicyData) -> dict | None:
    for b in data.blocks:
        hit = host == b["pattern"] or (b["kind"] == "suffix" and host.endswith("." + b["pattern"]))
        if hit and not any(e["pattern"] in (host, b["pattern"]) and e["source"] == b["source"] for e in data.exempt):
            return b
    return None


def decide(req: dict, policy: Policy, consents: ConsentStore,
           share_of: Callable[[str], str] = default_share, *, now: datetime | None = None) -> Decision:
    """``egress.request`` payload -> Decision. Pure: reads only its arguments."""
    data = policy.fresh()
    if policy.damaged or not policy.anchor.available:
        return Decision("deny", "floor", "bridge_closed", ledger_code="policy_damaged")
    if data.state != "open":
        return Decision("deny", "floor", "bridge_closed")
    subject = str(req.get("subject") or "")
    phase = req.get("phase") if req.get("phase") in PHASES else "run"
    dest = req.get("dest") if isinstance(req.get("dest"), dict) else {}
    host = hosts.clean(dest.get("host"))
    scheme = "http" if dest.get("scheme") == "http" else "https"
    port = dest.get("port") if isinstance(dest.get("port"), int) else (80 if scheme == "http" else 443)
    if subject.startswith(NO_GRANT_PREFIXES) or not subject:
        return Decision("deny", "floor", "not_on_list")
    if not data.switches.get(subject, {}).get("on"):
        return Decision("deny", "floor", "switched_off")
    # A .local or .lan name is treated like an address: only an exact LAN grant may match it (D110).
    lan_host = hosts.is_ip_literal(host) or hosts.is_local_name(host)
    if not lan_host and not hosts.is_dns_name(host):
        return Decision("deny", "floor", "not_on_list")
    when = now or datetime.now(timezone.utc)
    grant = None
    for g in data.grants:
        if g["subject"] != subject or g["phase"] != phase or g["port"] != port:
            continue
        if scheme not in _schemes(g):
            continue                          # a 443 grant never allows plain HTTP to 443
        if lan_host != bool(g["lan"]):
            continue
        if not (host == g["host"] if g["lan"] else hosts.covers(g["host"], g["subhosts"], host)):
            continue
        if g.get("until"):
            try:
                if datetime.fromisoformat(str(g["until"]).replace("Z", "+00:00")) < when:
                    continue
            except ValueError:
                continue
        rec = consents.granted(g["consent"])
        if rec is None or rec.get("sha") != g.get("consent_sha"):
            continue                          # the Yes behind it was revoked or changed
        grant = g
        break
    if grant is None:
        return Decision("deny", "floor" if lan_host else "policy", "not_on_list")
    block = _blocked(host, data)
    if block is not None:
        return Decision("deny", "policy", "blocked_by_provider", rule=str(block["source"])[:128])
    carries = req.get("carries") if isinstance(req.get("carries"), dict) else {}
    labels = [str(x) for x in carries.get("labels") or []]
    if labels:
        shares = []
        for label in labels:
            try:
                s = "never" if is_secret_label(label) else share_of(label)
            except Exception:  # noqa: BLE001 - an unreadable label effect is the strictest one
                s = "never"
            shares.append(s if s in SHARES else "never")
        worst = max(shares, key=SHARES.index)
        if worst == "never":
            return Decision("deny", "floor", "labelled")
        package = subject[4:] if subject.startswith("pkg:") else None
        p4 = carries.get("top_tier") == "P4"
        if worst == "ask_each":
            rec = consents.find_send(carries.get("payload_sha"), labels, None if p4 else package)
            if rec is None:
                return Decision("ask", "floor", "needs_yes")
            return Decision("allow", "owner", "granted", rule=rec["consent_id"])
        if worst == "ask_always":
            rec = None if p4 else consents.find_always(package, labels)
            if rec is None:
                return Decision("ask", "floor", "needs_yes")
            return Decision("allow", "owner", "granted", rule=rec["consent_id"])
    return Decision("allow", "policy", "granted", rule=grant["id"])


def grants_from_record(policy: Policy, rec: dict) -> list[dict]:
    """Turn one Yes record (kinds ``package``, ``host``, ``cloud``) into grants.

    ``covers``: ``hosts`` and ``install_hosts`` (``host[:port]``), ``subhosts``
    (domains granted with their sub-hosts), ``lan`` (exact ``address:port``, or
    a ``.local`` or ``.lan`` name and port), ``phase`` (for ``host`` and
    ``cloud``). Hosts that break the floor are refused before the card is
    shown (:func:`check_hosts`); here they raise.
    """
    kind = rec["scope"]["kind"]
    if kind not in ("package", "host", "cloud"):
        return []
    target = rec["scope"]["target"]
    subject = f"pkg:{target}" if kind == "package" and not target.startswith(("pkg:", "provider:")) else target
    cov = rec.get("covers") or {}
    if isinstance(cov.get("owner_address"), dict) and not owner_address_ok(kind, target, cov):
        raise GrantRefused("That Yes does not name one address and one port for one package.")
    default_phase = "build" if kind == "cloud" else cov.get("phase", "run")
    # A build plan's knowledge hosts (lane I11): the Core's own reads for that build, in the build phase only.
    # Only a Toolsmith build plan's Yes may carry them; on any other record they grant nothing.
    knowledge = list(cov.get("knowledge_hosts") or []) if kind == "package" \
        and str(target).startswith(KNOWLEDGE_TARGET) else []
    # Only the fixed list or a Data.gov file host, named exactly (no port, no scheme): anything else grants nothing.
    knowledge = [h for h in knowledge if knowledge_host_ok(h)]
    plain = list(cov.get("hosts") or []) + list(cov.get("install_hosts") or []) + knowledge
    subs, lans = list(cov.get("subhosts") or []), list(cov.get("lan") or [])
    problems = check_hosts(plain) + check_hosts(subs, subhosts=True) + check_hosts(lans, lan=True)
    if problems:
        raise GrantRefused(" ".join(problems))  # all or nothing: never half the hosts of one Yes
    out = []
    for field_name, phase in (("hosts", default_phase), ("install_hosts", "install")):
        for item in cov.get(field_name) or []:
            sch, h, p = hosts.split_grant(item)
            out.append(policy.add_grant(subject, h, p, phase=phase, consent=rec, schemes=sch))
    for item in knowledge:
        sch, h, p = hosts.split_grant(item)
        out.append(policy.add_grant(subject, h, p, phase="build", consent=rec, schemes=["https"]))
    for item in subs:
        sch, h, p = hosts.split_grant(item)
        out.append(policy.add_grant(subject, h, p, phase=default_phase, consent=rec, subhosts=True, schemes=sch))
    for item in lans:
        sch, h, p = hosts.split_grant(item)
        out.append(policy.add_grant(subject, h, p, phase=default_phase, consent=rec, lan=True, schemes=sch))
    return out


def owner_address_ok(kind: Any, subject: Any, covers: dict) -> bool:
    """Does this Yes grant exactly the one address it names (D110)? A host Yes for ``pkg:<its package>``, run phase,
    carrying that one ``host:port`` (in ``hosts`` for ``where: internet``, else in ``lan``) and nothing else, unpinned,
    with a card whose sentence names that same address, port and kind."""
    owned = covers.get("owner_address")
    if kind != "host" or not isinstance(owned, dict):
        return False
    address, port, package = owned.get("address"), owned.get("port"), owned.get("package")
    if not isinstance(address, str) or not isinstance(package, str) or isinstance(port, bool) or not isinstance(port, int):
        return False
    item = hosts.grant_item(address, port)
    internet = owned.get("where") == "internet"
    if "where" in owned and not internet:
        return False
    card = covers.get("card") if isinstance(covers.get("card"), dict) else {}
    if not str(card.get("sentence") or "").endswith(" " + hosts.address_words(address, port, internet)):
        return False
    if covers.get("raised_by") not in (None, "owner", "package"):         # who raised it; it grants nothing
        return False
    return (subject == f"pkg:{package}" and covers.get("phase") == "run"
            and list(covers.get("hosts") or []) == ([item] if internet else [])
            and list(covers.get("lan") or []) == ([] if internet else [item])
            and not any(covers.get(k) for k in ("install_hosts", "subhosts", "knowledge_hosts")))


def check_hosts(items: Iterable[str], *, subhosts: bool = False, lan: bool = False) -> list[str]:
    """Plain problems with hosts a card would grant ([] = fine). Run before the card is raised."""
    problems = []
    for item in items:
        try:
            _s, h, _p = hosts.split_grant(item)
        except ValueError:
            problems.append(f"{item!r} is not a host and port.")
            continue
        if "*" in str(item):
            problems.append(f"{item!r}: a grant never uses a wildcard.")
        elif lan and not hosts.is_lan_host(h):
            problems.append(f"{item!r} is not a home-network address.")
        elif subhosts and not hosts.subhost_grant_ok(h):
            problems.append(f"{h} is shared by many sites, so it can't be granted with all its sub-hosts.")
        elif not lan and not hosts.is_dns_name(h):
            problems.append(f"{item!r} is not a plain internet name.")
    return problems


def knowledge_host_ok(host: Any) -> bool:
    """May a build plan's ``knowledge_hosts`` name *host*? One of :data:`KNOWLEDGE_HOSTS`, or a plain DNS name ending
    ``.gov`` (Data.gov's file hosts, as ``data.usgs.gov``): no port, no scheme, no wildcard, no address, and it
    passes the floor (:func:`check_hosts`)."""
    if not isinstance(host, str):
        return False
    return host in KNOWLEDGE_HOSTS or (_GOV_HOST.fullmatch(host) is not None and not check_hosts([host]))


__all__ = ["KNOWLEDGE_HOSTS", "KNOWLEDGE_TARGET", "READ_PUBLIC_PAGES", "READ_SUBJECT", "SEARCH_HOSTS", "TRY_FOUND_APPS", "WEB_KEY_HOSTS", "Decision", "GrantRefused", "Policy", "PolicyData", "check_hosts", "decide", "default_share",
           "grants_from_record", "is_secret_label", "knowledge_host_ok", "owner_address_ok", "reads_public_yes", "tries_found_yes"]
