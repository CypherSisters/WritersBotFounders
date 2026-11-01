"""One address and port for one package, with its own plain Yes (D110).

Store packages reach the outside only through the Bridge. This is how the owner lets one of them reach one more place:
a device on her home network (``lan``), or one exact site on the internet (``internet``). A package may ask, or she may
type it herself on the Bridge page; either way it is the same Yes card, with a tap.

**Nothing here grants.** It raises a card (``consents.ask("host", "pkg:<id>", covers)``). Only her Yes makes a record,
and the Bridge's own Yes listener makes the grant from that record's covers, which ``card_sha`` and the row's seal cover
(:func:`.policy.owner_address_ok` refuses a record that names one address and carries another). ``build_of`` is None for
it, so it is a normal run grant and survives a Core restart. A package never sees a card's id or fingerprint.

What is accepted (:func:`parse`), strictly, because this text comes from a package or a paste:

- ``lan``: a private address in its one plain spelling, or an exact name ending in ``.local`` or ``.lan``.
- ``internet``: an exact public DNS name (``hosts.is_dns_name``), never its sub-hosts, a wildcard, or a public address
  given as numbers.
- Always a port: typed, or implied by a typed scheme (``https://`` 443, ``http://`` 80), so the card can name it. Which
  schemes the grant allows comes from the port (``hosts.default_schemes``), not from what was typed.
- This computer needs no Bridge step: loopback and ``localhost`` are answered :class:`NotNeeded`, with no card.

No line here ever repeats what was typed, so a pasted key or password can't reach a line, a log or an error. A name
that looks like a key gets no card.

**Nagging (:func:`ask`).** One waiting card per package: the same address returns that card, another replaces it. A
package gets at most :data:`MAX_CARDS_PER_HOUR` new cards an hour, and a (package, address, port) she declined is not
raised again for an hour. Those limits read the consent store itself, so a restart resets nothing. They hold a package
back, never her: her own form is her own act.

**Who raised a card (``covers.raised_by``, so the card's seal covers it).** A package's ask never closes a card she
raised: while one of hers waits for another address, it gets a plain refusal and no card. Its different address still
replaces its own waiting card. Her form replaces any waiting card of that package, and takes over a package's card for
the same address, so nothing can close the card it just showed her. Only cards a package raised count toward its limit.
A record with no marker counts as the package's, and is never treated as hers.

**A package never takes a Yes back.** A Yes whose grant is not there is what a package sees in the moment between her Yes
and the Bridge's listener, and after a damaged policy file. Only her own form may end such a Yes and ask again, and only
when the policy reads clean. A package hears "waiting", with no card.

**Removing (:func:`take_back`, :func:`take_back_package`)** revokes the Yes and drops its grant, one action. Removing a
package does that for each of its addresses, and a reinstall asks again; an update keeps them (same id, same address).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from ..anchor import AnchorUnavailable
from ..zone1 import secret_detector
from . import hosts
from .drawbridge import _safe_name

logger = logging.getLogger(__name__)
LAN = "lan"
INTERNET = "internet"
KINDS = (LAN, INTERNET)
MAX_TYPED = 300
MAX_CARDS_PER_HOUR = 5
HOUR = timedelta(hours=1)
SOME_PACKAGE = "This package"
BY_OWNER = "owner"
BY_PACKAGE = "package"

NOT_NEEDED_LINE = "Not needed: a service on this computer needs no Bridge step."
PLAIN_LINE = "Give just the address and port, like 192.168.1.20:8123. No path, no user name, no wildcard and no spaces."
PORT_LINE = "Add the port, like 192.168.1.20:8123, or start with http:// or https://. The card always names the port."
BAD_PORT_LINE = "The port is a number from 1 to 65535."
ODD_LINE = "That address is written in an unusual way, so it is refused. Write it plainly, like 192.168.1.20."
PUBLIC_NUMBER_LINE = "A public address given as numbers can't be allowed. Use the site's name and a port."
NOT_HOME_LINE = "That address can't be allowed: it is not a device on your home network."
NOT_NAME_LINE = "That name can't be allowed. Use a site's full name, or a home name ending in .local or .lan."
KEYISH_LINE = "That looks like a key or a token, not an address, so it was refused."
NOT_DECLARED_LINE = "This package did not declare that it asks for addresses, so nothing was raised. The owner can allow one on the Bridge page."
WRONG_KIND = {LAN: "This package declared that it asks for addresses on the home network, so it can't ask for this one.",
              INTERNET: "This package declared that it asks for addresses on the internet, so it can't ask for this one."}
DECLINED_LINE = "The owner said No to this address and port. It is not raised again for an hour."
TOO_MANY_LINE = "This package has asked for too many addresses this hour, so no new card was raised. It can ask again later."
NOT_FOR_LINE = "That package can't be allowed an address."
OWNER_WAITING_LINE = "The owner has another address waiting for a Yes for this package, so no card was raised. It can ask again later."
REPLACED_LINE = "A different address was asked for, so this card closed."
REPLACED_BY_OWNER_LINE = "You asked for an address on the Bridge page, so this card closed and a new one took its place."
STALE_LINE = "The owner's Yes for this address can't be used right now. The owner can remove it on the Bridge page and allow it again."

_REST = re.compile(r"[A-Za-z0-9.:\[\]-]+")
_PORT = re.compile(r"[1-9][0-9]{0,4}")
_SCHEME = re.compile(r"(https?)://(.*)", re.IGNORECASE | re.DOTALL)
#: Dots NFKC leaves as they are, or other scripts' dots: each reads as a dot in a place's name on the card.
_DOTS = str.maketrans(dict.fromkeys(map(chr, (0x00B7, 0x0387, 0x0589, 0x06D4, 0x0701, 0x0702, 0x1362, 0x2024, 0x2027, 0x2219,
                                              0x22C5, 0x3002, 0x30FB, 0xA4F8, 0xA60E)), "."))
_COMMAS = "," + chr(0x3001) + chr(0x060C)
#: A name that reads like an address on the card (package text beside the real address) is not said. Read after NFKC, with
#: every dot a dot: a dotted name or number, a part of an address, numbers split by spaces or commas, the card's own words.
_NAME_LOOKS_LIKE_A_PLACE = re.compile(rf"\d\.\d|[\w-]\.\w{{2,}}|[\[\]:/@\\]|\d+(?:[\s{_COMMAS}]+\d+)+"
                                      r"|\bport\s*\d|\bhome\s+network\b|\bon\s+the\s+internet\b", re.IGNORECASE)
_LOCK = threading.Lock()


class Refused(ValueError):
    """No card: ``line`` says why, plainly, and never repeats what was typed."""

    def __init__(self, line: str) -> None:
        super().__init__(line)
        self.line = line


class NotNeeded(Refused):
    """A service on this computer: no card, and nothing to do."""

    def __init__(self) -> None:
        super().__init__(NOT_NEEDED_LINE)


@dataclass(frozen=True)
class Address:
    host: str
    port: int
    where: str                     # lan | internet

    @property
    def item(self) -> str:
        return hosts.grant_item(self.host, self.port)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(text: Any) -> datetime | None:
    try:
        got = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=timezone.utc)


def _keyish(text: str) -> bool:
    try:
        return bool(secret_detector.find(text))
    except Exception:  # noqa: BLE001 - fail closed: treated as a key
        return True


def _where_of(host: str) -> str:
    """``lan`` or ``internet`` for a clean host, or :class:`Refused`. Parsed, never pattern-matched: an address in an
    odd spelling is refused here (``0x7f.1``, ``127.1``, ``2130706433``, ``::ffff:127.0.0.1``)."""
    if host == "localhost" or host.endswith(".localhost"):
        raise NotNeeded()
    if hosts.is_ip_literal(host):
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            raise Refused(ODD_LINE) from None
        if str(ip) != host or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped):
            raise Refused(ODD_LINE)
        if ip.is_loopback:
            raise NotNeeded()
        if hosts.is_lan_host(host):
            return LAN
        raise Refused(PUBLIC_NUMBER_LINE if hosts.is_public(host) else NOT_HOME_LINE)
    if hosts.is_local_name(host):
        return LAN
    if hosts.is_dns_name(host):
        return INTERNET
    raise Refused(NOT_NAME_LINE)


def parse(text: Any) -> Address:
    """The one exact place in what was typed, or :class:`Refused`. ``http://x:8123`` and ``x:8123`` are the same place."""
    if not isinstance(text, str) or not text or len(text) > MAX_TYPED or not text.isascii() or not text.isprintable():
        raise Refused(PLAIN_LINE)
    implied = None
    rest = text
    if "://" in text:
        scheme = _SCHEME.fullmatch(text)
        if scheme is None:
            raise Refused(PLAIN_LINE)
        implied = 443 if scheme.group(1).lower() == "https" else 80
        rest = scheme.group(2)
    if not _REST.fullmatch(rest):                  # no path, query, user part, wildcard, space, zone or anything odd
        raise Refused(PLAIN_LINE)
    brackets = False
    typed_port = None
    if rest.startswith("["):
        got = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\](?::(.+))?", rest)
        if got is None:
            raise Refused(PLAIN_LINE)
        host, typed_port, brackets = got.group(1), got.group(2), True
    elif rest.count(":") == 1:
        host, typed_port = rest.split(":")
    else:
        host = rest                                # no colon, or a bare IPv6 address, which can't carry a port
    if "[" in host or "]" in host or not host or host.endswith("."):
        raise Refused(ODD_LINE if host else PLAIN_LINE)
    host = host.lower()
    if brackets and ":" not in host:
        raise Refused(ODD_LINE)                    # brackets are for IPv6 only
    where = _where_of(host)
    if _keyish(text):
        raise Refused(KEYISH_LINE)
    if typed_port is not None:
        if not _PORT.fullmatch(typed_port) or int(typed_port) > 65535:
            raise Refused(BAD_PORT_LINE)
        port = int(typed_port)
    elif implied is not None and (":" not in host or brackets):
        port = implied
    else:
        raise Refused(PORT_LINE)
    return Address(host, port, where)


def safe_name(name: Any) -> str:
    """A package's name on the card: Drawbridge's own screen (one line, no words aimed at an AI, no link), and nothing
    that does not print or reads like a place, so package text can't look like the address she is asked about. It is read
    as it looks (NFKC, every dot a dot), so fullwidth digits and other scripts' dots read as what they imitate. A screen, not
    a proof: the card always names the real address in the Core's own words, right after the name."""
    said = _safe_name({"name": name})
    if not said.isprintable():
        return SOME_PACKAGE
    if _NAME_LOOKS_LIKE_A_PLACE.search(unicodedata.normalize("NFKC", said).translate(_DOTS)):
        return SOME_PACKAGE
    return said


def covers_for(package: str, name: str, address: Address, *, closed: bool, raised_by: str = BY_PACKAGE) -> dict:
    """What the Yes covers and what the card says, all from one parsed address. No package text but its name and id.
    *raised_by* changes no word on the card and no grant: it says only who may close the card, and whose limit it counts in."""
    said = safe_name(name)
    owned: dict[str, Any] = {"package": package, "address": address.host, "port": address.port}
    internet = address.where == INTERNET
    if internet:
        owned["where"] = INTERNET
    sentence = f"{said} ({package}) asks to reach " + hosts.address_words(address.host, address.port, internet)
    lines = ["What this package sends goes to that site, and that site sees your home's internet address."] if internet else []
    lines += [f"Only {said}, only this address and this port. Nothing else opens.", "Remove it any time on the Bridge page."]
    if closed:
        lines.append("The Bridge is closed now, so this works only once you open it.")
    covers: dict[str, Any] = {"phase": "run", "owner_address": owned, "raised_by": raised_by,
                              "card": {"title": f"Allow this address and port for {said}?", "sentence": sentence, "lines": lines}}
    covers["hosts" if internet else "lan"] = [address.item]
    return covers


def package_of(rec: dict) -> str | None:
    """The package an address record is for, or None when it is any other record."""
    scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
    owned = covers.get("owner_address") if isinstance(covers.get("owner_address"), dict) else None
    if scope.get("kind") != "host" or owned is None or not isinstance(owned.get("package"), str):
        return None
    return owned["package"] if scope.get("target") == f"pkg:{owned['package']}" else None


def _hers(rec: dict) -> bool:
    """Raised by her own form. No marker, or any other value, is not hers."""
    return (rec.get("covers") or {}).get("raised_by") == BY_OWNER


def _same(rec: dict, address: Address) -> bool:
    owned = (rec.get("covers") or {}).get("owner_address") or {}
    return owned.get("address") == address.host and owned.get("port") == address.port


def cards(consents: Any, package: str | None = None, status: str | None = None) -> list[dict]:
    """Every address card or record, oldest first: of one package, in one status."""
    return [r for r in consents.list(status) if package_of(r) and package in (None, package_of(r))]


def has_live(consents: Any, package: str) -> bool:
    return any(package_of(r) == package for r in consents.live("host"))


def _has_grant(policy: Any, consent_id: str) -> bool | None:
    """Does a grant from this Yes stand? None when the policy can't be read: damaged and no machine key both read as an
    empty policy, and an empty reading says nothing about her Yes."""
    try:
        data = policy.fresh()
    except (AnchorUnavailable, OSError):
        return None
    if policy.damaged or not policy.anchor.available:
        return None
    return any(g.get("consent") == consent_id for g in data.grants)


def ask(consents: Any, policy: Any, package: str, name: str, address: Address, *, closed: bool, by_package: bool) -> dict:
    """``{"state": "allowed"}``, or ``{"state": "waiting", "record": rec}``, raising a card only if none waits for this
    address, or ``{"state": "waiting", "record": None, "line": ...}`` for a Yes whose grant is not there and that only her
    own form may end. :class:`Refused` when a limit says no card (a package only). Never grants."""
    with _LOCK:
        mine = cards(consents, package)
        now = _now()
        for rec in mine:
            if rec["status"] == "granted" and _same(rec, address) and consents.granted(rec["consent_id"]):
                held = _has_grant(policy, rec["consent_id"])
                if held:
                    return {"state": "allowed"}
                if by_package or held is None:         # never a package's doing, and a can't-tell is never a no
                    return {"state": "waiting", "record": None, "line": STALE_LINE}
                consents.revoke(rec["consent_id"])     # her own form, the policy clean, the grant gone: she asks again
        waiting = [r for r in mine if r["status"] == "pending"]
        same = [r for r in waiting if _same(r, address)]
        if same and (by_package or _hers(same[-1])):
            return {"state": "waiting", "record": same[-1]}
        if by_package:
            if any(_hers(r) for r in waiting):         # a package never closes a card she raised
                raise Refused(OWNER_WAITING_LINE)
            for rec in mine:
                when = _when(rec.get("answered_at"))
                if rec["status"] == "declined" and _same(rec, address) and when is not None and now - when < HOUR:
                    raise Refused(DECLINED_LINE)
            recent = [r for r in mine if not _hers(r)
                      and (lambda t: t is not None and now - t < HOUR)(_when(r.get("created_at")))]
            if len(recent) >= MAX_CARDS_PER_HOUR:
                raise Refused(TOO_MANY_LINE)
        for rec in waiting:
            consents.expire(rec["consent_id"], REPLACED_LINE if by_package else REPLACED_BY_OWNER_LINE)
        rec = consents.ask("host", f"pkg:{package}", covers_for(package, name, address, closed=closed,
                                                                raised_by=BY_PACKAGE if by_package else BY_OWNER))
        return {"state": "waiting", "record": rec}


def drop_grant(drop: Callable[[str, str], Any] | None, consent_id: str, package: str | None) -> None:
    """Drop the grant of a Yes already taken back. If it can't go now (the policy file in use), that is logged and nothing
    more: a revoked Yes stops the Bridge on its own, and removing the package clears the row later."""
    if drop is None or not package:
        return
    try:
        drop(consent_id, f"pkg:{package}")
    except Exception:  # noqa: BLE001 - never undoes her act, and the next address still goes
        logger.exception("the grant of an address Yes was not dropped")


def take_back(consents: Any, rec: dict, drop: Callable[[str, str], Any] | None, note: str) -> bool:
    """One card or Yes goes, and its grant, as one action. The Yes first: revoked, it stops the Bridge on its own."""
    cid, package = rec["consent_id"], package_of(rec)
    done = False
    if rec.get("status") == "pending":
        done = consents.expire(cid, note)
    elif rec.get("status") == "granted":
        done = consents.revoke(cid)
    drop_grant(drop, cid, package)
    return done


def take_back_package(consents: Any, package: str, drop: Callable[[str, str], Any] | None) -> int:
    """All of a package's addresses go (it is being removed, or installed fresh). Any grant left by an older Yes too."""
    done = 0
    for rec in cards(consents, package):
        done += bool(take_back(consents, rec, drop, "This package was removed."))
    return done


def rows(consents: Any, view: dict, names: dict[str, str], blocked: Callable[[str], bool] | None = None) -> list[dict]:
    """The Bridge page's rows: every address waiting for her Yes or allowed, with what is true of it now. *view* is
    :meth:`.bridge.Bridge.view`: a grant there is usable now (setting open, switch on, its Yes live). *blocked(host)*: a
    block list stops this host, so it reads waiting even with a grant."""
    switches = view.get("switches") or {}
    live = {(x.get("subject"), x.get("host"), x.get("port")) for x in view.get("live") or []
            if x.get("from") == "grant" and x.get("phase") == "run"}
    out = []
    for rec in cards(consents):
        if rec["status"] not in ("pending", "granted"):
            continue
        owned = rec["covers"]["owner_address"]
        package, address, port = owned["package"], owned.get("address"), owned.get("port")
        name = safe_name(names.get(package) or package)
        subject = f"pkg:{package}"
        if rec["status"] == "pending":
            state, line = "waiting", "Waiting for your Yes."
        elif consents.granted(rec["consent_id"]) is None:
            continue
        elif (subject, address, port) in live:
            if blocked is not None and blocked(str(address)):
                state, line = "waiting", "Waiting: a block list you use stops this address."
            else:
                state, line = "on", f"On. Only {name} may reach this."
        elif view.get("setting") != "open":
            state, line = "waiting", "Waiting: the Bridge is closed, so this works once you open it."
        elif subject in switches and not switches[subject]:
            state, line = "waiting", f"Waiting: {name} is switched off on the Bridge."
        else:
            state, line = "waiting", "Waiting: this can't be used now. Remove it and allow it again."
        internet = owned.get("where") == INTERNET
        out.append({"consent_id": rec["consent_id"], "package": package, "name": name, "address": address,
                    "port": port, "where": INTERNET if internet else LAN,
                    "where_said": "internet" if internet else "home network", "state": state, "line": line})
    return out


__all__ = ["Address", "BY_OWNER", "BY_PACKAGE", "INTERNET", "KINDS", "LAN", "MAX_CARDS_PER_HOUR", "NotNeeded", "Refused", "ask", "cards",
           "covers_for", "drop_grant", "has_live", "package_of", "parse", "rows", "safe_name", "take_back", "take_back_package"]
