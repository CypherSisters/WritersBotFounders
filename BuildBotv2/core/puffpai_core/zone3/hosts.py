# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/box/assets/egress_proxy.py (adapted: is_public, looks_like_ip, HOST_RE)
# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/egress_doors.py (adapted: is_ip_literal, is_registrable_hostname)
"""Host names for the Bridge: what a grant may name, and what a name may resolve to.

Rules (Z3 section 2, D28(9)):
- A host is a DNS name, lower case, at least two labels, never an address in
  any spelling (``8.8.8.8``, ``127.1``, ``0x7f000001``, ``[::1]``) and never a
  local-network name (``.local``, ``.lan``, ``localhost``): :func:`is_dns_name`
  refuses all of them.
- A grant names one exact ``host:port``, or one registrable domain and its
  sub-hosts (``github.com`` covers ``api.github.com``). A grant never covers a
  public suffix: ``com``, ``co.uk`` and shared-hosting suffixes like
  ``github.io`` are refused, so ``*.com`` can't be written in any form.
- A LAN grant is the one exception: an exact private ``address:port``, or an
  exact name ending in ``.local`` or ``.lan`` (:func:`is_local_name`) and a port,
  the owner typed or picked (``lan: true``, D110). It never has sub-hosts. A name
  is resolved once at dial and only home-network answers are dialled
  (:func:`local_answers`): a public answer refuses the whole name.

Public suffixes come from the full Public Suffix List (ICANN and private
sections, wildcards and exceptions), bundled offline as package data in
``zone3/data/public_suffix_list.dat`` (source https://publicsuffix.org/list/,
version 2026-10-20, fetched 2026-10-22; see ``zone3/data/README.md``). Nothing
is fetched at runtime. A few tunnel and shared-hosting names the list may not
carry are added (:data:`EXTRA_SHARED`). If the bundled file can't be read,
every domain counts as a public suffix: no sub-host grant at all (fail closed).

Schemes: a grant on port 443 allows CONNECT (TLS) only, on port 80 plain HTTP
only, on any other port both (:func:`default_schemes`).
"""

from __future__ import annotations

import functools
import ipaddress
import re
from importlib import resources

HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".home.arpa", ".internal", ".intranet", ".corp")
LOCAL_TLDS = frozenset(s.rsplit(".", 1)[-1] for s in LOCAL_SUFFIXES) | {"localhost", "localdomain", "arpa"}

EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "224.0.0.0/4", "240.0.0.0/4", "::/128", "::1/128", "fc00::/7", "fe80::/10",
    "ff00::/8", "64:ff9b::/96", "2002::/16",
    # Old or special ranges: Python's own tables miss some, and what they hold moves between versions.
    "192.88.99.0/24", "fec0::/10", "100::/64", "2001:db8::/32", "64:ff9b:1::/48", "5f00::/16", "3fff::/20")]

#: Shared-hosting and tunnel suffixes added to the Public Suffix List (never shrinks it).
EXTRA_SHARED = frozenset("""
github.io githubusercontent.com gitlab.io pages.dev workers.dev vercel.app netlify.app
herokuapp.com azurewebsites.net cloudapp.net blob.core.windows.net web.core.windows.net
appspot.com firebaseapp.com web.app cloudfront.net amazonaws.com s3.amazonaws.com
elasticbeanstalk.com blogspot.com wordpress.com tumblr.com glitch.me repl.co fly.dev
onrender.com railway.app deno.dev surge.sh ngrok.io ngrok-free.app trycloudflare.com
duckdns.org no-ip.org ddns.net dyndns.org hf.space modal.run pipedream.net loca.lt
""".split())
PSL_FILE = "public_suffix_list.dat"
SCHEMES = ("http", "https")
#: The only names a LAN grant may carry (D110): ``.home``, ``.internal`` and the rest of LOCAL_SUFFIXES stay refused.
LAN_NAME_SUFFIXES = (".local", ".lan")
#: What counts as the home network. Python's own ``is_private`` also says yes to this-network, broadcast, reserved,
#: documentation and benchmark ranges, none of which is a device in a home.
HOME_NETWORKS = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))


def _ascii(name: str) -> str | None:
    try:
        return ".".join(label if label.isascii() else label.encode("idna").decode("ascii")
                        for label in name.lower().split("."))
    except (UnicodeError, ValueError):
        return None


@functools.lru_cache(maxsize=1)
def _psl() -> tuple[frozenset, frozenset, frozenset] | None:
    """``(rules, wildcard parents, exceptions)`` from the bundled list, or None if unreadable."""
    try:
        text = resources.files(__package__).joinpath("data", PSL_FILE).read_text(encoding="utf-8")
    except (OSError, ValueError, ModuleNotFoundError):
        return None
    rules, wild, exc = set(), set(), set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        rule = line.split()[0]
        if rule.startswith("!"):
            name = _ascii(rule[1:])
            if name:
                exc.add(name)
        elif rule.startswith("*."):
            name = _ascii(rule[2:])
            if name:
                wild.add(name)
        else:
            name = _ascii(rule)
            if name:
                rules.add(name)
    if len(rules) < 1000:
        return None                    # not the real list: fail closed
    return frozenset(rules), frozenset(wild), frozenset(exc)


def psl_loaded() -> bool:
    return _psl() is not None


def clean(host: object) -> str:
    """Lower case, no trailing dot, no brackets. "" if not a string."""
    if not isinstance(host, str):
        return ""
    return host.strip().strip("[]").rstrip(".").lower()


def is_ip_literal(host: object) -> bool:
    """True if *host* is an address in ANY notation (parsed, not pattern-matched)."""
    if not isinstance(host, str):
        return False
    text = host.strip()
    if not text:
        return False
    if text.startswith("[") or text.endswith("]") or ":" in text:
        return True
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        pass
    parts = text.split(".")
    if len(parts) > 4:
        last = parts[-1].lower()
        return last.isdigit() or last.startswith("0x")
    for part in parts:
        if not part:
            return False
        low = part.lower()
        try:
            if low.startswith("0x"):
                int(low, 16)
            elif low.startswith("0") and len(low) > 1:
                int(low, 8)
            else:
                int(low, 10)
        except ValueError:
            return False
    return True


def is_dns_name(host: object) -> bool:
    """A public DNS name the Bridge may grant: shape, two labels, a real TLD, not local, not an address."""
    h = clean(host)
    if not h or not HOST_RE.match(h):
        return False
    if is_ip_literal(h):
        return False
    if h == "localhost" or h.endswith(LOCAL_SUFFIXES):
        return False
    labels = h.split(".")
    return len(labels) >= 2 and labels[-1] not in LOCAL_TLDS and bool(_TLD_RE.match(labels[-1]))


def is_local_name(host: object) -> bool:
    """An exact home-network name a LAN grant may carry: DNS shape, ending in ``.local`` or ``.lan``, not an address."""
    h = clean(host)
    return bool(h) and bool(HOST_RE.match(h)) and not is_ip_literal(h) and h.endswith(LAN_NAME_SUFFIXES)


def is_public_suffix(host: str) -> bool:
    """True when *host* is itself a public suffix (PSL rules, wildcards, exceptions; every TLD)."""
    h = clean(host)
    if not h or "." not in h or h in EXTRA_SHARED:
        return True
    psl = _psl()
    if psl is None:
        return True                    # no list: nothing may be granted with its sub-hosts
    rules, wild, exc = psl
    if h in exc:
        return False
    if h in rules:
        return True
    return h.split(".", 1)[1] in wild


def default_schemes(port: int) -> tuple[str, ...]:
    """443: https (CONNECT) only; 80: http only; any other port: both."""
    if port == 443:
        return ("https",)
    if port == 80:
        return ("http",)
    return SCHEMES


def split_grant(text: str) -> tuple[list[str] | None, str, int]:
    """``[http://|https://]host[:port]`` -> ``(schemes or None, host, port)``. Raises ``ValueError``."""
    t = str(text or "").strip()
    schemes = None
    for scheme, port in (("http", 80), ("https", 443)):
        if t.lower().startswith(scheme + "://"):
            rest = t[len(scheme) + 3:]
            if any(c in rest for c in "/?#@\\"):
                raise ValueError("a grant names a host and port, never a path")
            host, p = split_hostport(rest, default_port=port)
            return [scheme], host, p
    host, p = split_hostport(t)
    return schemes, host, p


def subhost_grant_ok(domain: str) -> bool:
    """True when *domain* may be granted with all its sub-hosts (D28(9)): a DNS
    name that is not itself a public suffix. Wildcard characters never pass."""
    d = clean(domain)
    if "*" in str(domain) or not is_dns_name(d) or is_public_suffix(d):
        return False
    return True


def covers(grant_host: str, subhosts: bool, host: str) -> bool:
    """Does a grant for *grant_host* (with or without sub-hosts) cover *host*?"""
    g, h = clean(grant_host), clean(host)
    if not g or not h:
        return False
    if g == h:
        return True
    return subhosts and h.endswith("." + g)


def is_public(addr: str) -> bool:
    """A public unicast address (not private, loopback, link-local, CGNAT, multicast or reserved)."""
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        return False
    return not any(ip in net for net in EXTRA_BLOCKED if net.version == ip.version)


def is_private_address(addr: str) -> bool:
    """A home-network address a LAN grant may name: 10/8, 172.16/12, 192.168/16 or fc00::/7, and nothing else, so
    never loopback, link-local, this-network, multicast, broadcast or a reserved range."""
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in net for net in HOME_NETWORKS if net.version == ip.version)


def is_lan_host(host: object) -> bool:
    """What a LAN grant may carry: a name (:func:`is_local_name`), or a private address in its one plain spelling
    (no IPv4-mapped form, no zone, no other way of writing it)."""
    h = clean(host)
    if is_local_name(h):
        return True
    if "%" in h:                       # Python reads a zone as part of an IPv6 address, and it prints back the same
        return False
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    return str(ip) == h and is_private_address(h) and not (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped)


def local_answers(addrs: list[str]) -> tuple[list[str], str]:
    """What a ``.local`` or ``.lan`` name's answers leave to dial: ``(kept, "")``, or ``([], ledger code)``.

    Any public answer refuses the whole name: ``.lan`` can leak to public DNS, and a rebinding name must not pass.
    Only home-network answers are kept, so loopback and the metadata address are never dialled, and Windows' ``fe80::``
    beside the IPv4 of a ``.local`` name is dropped, not fatal. None left is refused."""
    if not addrs:
        return [], "cannot_look_up"
    if any(is_public(a) for a in addrs):
        return [], "lan_only"
    kept = list(dict.fromkeys(a for a in addrs if is_private_address(a)))
    if kept:
        return kept, ""
    if all(is_loopback(a) or a in ("0.0.0.0", "::") for a in addrs):
        return [], "network_filter"        # what a home DNS filter answers for a blocked name
    return [], "inside_address"


def grant_item(host: str, port: int) -> str:
    """``host:port`` as a grant writes it, with an IPv6 address in brackets."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def address_words(host: str, port: int, internet: bool) -> str:
    """How a Yes card names one address (D110), and what :func:`.policy.owner_address_ok` checks it said."""
    return f"{host} port {port} " + ("on the internet." if internet else "on your home network.")


def is_loopback(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def split_hostport(text: str, default_port: int = 443) -> tuple[str, int]:
    """``host[:port]`` -> ``(host, port)``. Raises ``ValueError`` on junk."""
    t = str(text or "").strip()
    if t.startswith("["):
        end = t.find("]")
        if end < 0:
            raise ValueError("bad address")
        host, rest = t[1:end], t[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") else default_port
    elif t.count(":") == 1:
        host, p = t.split(":")
        port = int(p)
    elif ":" in t:
        host, port = t, default_port            # a bare IPv6 address
    else:
        host, port = t, default_port
    if not 0 < port < 65536:
        raise ValueError("bad port")
    return clean(host), port


__all__ = ["EXTRA_SHARED", "HOME_NETWORKS", "HOST_RE", "LAN_NAME_SUFFIXES", "SCHEMES", "address_words", "clean", "default_schemes", "grant_item", "psl_loaded", "split_grant", "covers",
           "is_dns_name", "is_ip_literal", "is_lan_host", "is_local_name", "is_loopback", "is_private_address", "is_public", "is_public_suffix", "local_answers",
           "split_hostport", "subhost_grant_ok"]
