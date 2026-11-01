"""The home network (D130, built, not yet tested by the owner): a paired device opens the Desktop from another computer.

On unless she turns it off (:data:`DEFAULT_ON`), saved per bot in ``<bot folder>/settings/network.json``. Off means no
listener and no socket. On means one more listener beside the two on 127.0.0.1, which stay exactly as they were. Only a
device she has paired gets in, and a Core that can't bind it still starts and says so:
- the network listener (the Core's port + 3, :func:`network_ports`): the Desktop and the Core API for a paired browser;
- one page listener per package, opened when a paired device asks (``GET /v1/network/pages``). D53 needs each package
  its own origin, and ``<id>.localhost`` does not resolve on another device. Each port is one origin, so one
  localStorage: a page there can never read the Desktop's second half.

The stamp (``puffpai.listener``) and the package id of a page listener are set by the server, never from a header.

Before any route runs (``api.app._page_hosts`` calls :meth:`Network.refuse`):
- the setting must be on;
- the socket's real peer must be home-network or link-local, never public, never loopback. No forwarded header is read;
- ``Host`` must be this computer's own home-network address or its own name, on the listener's port (DNS rebinding).

In a container with published ports and no host networking, the bot sees only the container's own address, not the one her
browser uses. :data:`ADDRESSES_ENV` names what she really uses (:func:`own_addresses`, :func:`own_names`). It can't widen
who may connect, only which ``Host`` a request may carry. The port in ``Host`` must still equal the listener's own, so the
host has to publish the same numbers: ``-p 8433:8433``, and each page port the same way (:func:`network_ports`). The peer
check needs no change for this: a published port shows the Docker gateway (the userland proxy) or the client's own
home-network address (DNAT), and both pass :func:`peer_allowed`. Through the userland proxy every client has the gateway's
address, so there the peer check can't tell where a connection really came from, and :class:`PairLimit` counts all of
them as one device.

Then the gates in ``api.app`` hold: only a paired browser's two halves count, and owner keys and package and Toolsmith
tokens are refused whatever they could do on 127.0.0.1. Past that a paired device gets everything the owner gets on this
computer (the owner's decision): "bots can be reached by any paired device with full functionality." Roles come later.
Only what must happen on a screen differs: a file comes to the browser that asked, and a window or console is not opened.

A page listener never sees the second half. Each package gets a page pass (``auth.TokenStore.page_pass``): an HttpOnly
cookie, one per page port, signed with that device's own binding, for that one package, good for 12 hours. The Desktop
asks for them with both halves. A cookie reaches every port of a host, so the Desktop's own answer can set it, and nothing
but the cookie has to travel: no pass in an address, so none in a Referer or a page's own script. The Core takes every
cookie out before it forwards to a package (``groups.pages``), and a removed device's passes stop with it.

There is no TLS. Anyone watching the network can see the code and the cookies. See the Settings tile's words,
:data:`WORTH_KNOWING`.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import jsonfile
from ..packages.manifest import adopted_origin
from ..zone3.hosts import HOME_NETWORKS

#: Whether a bot with no saved choice listens on its home network: THE one place. The owner's decision: on, because "a
#: desktop should be able to work from any paired device". Her saved Off always wins over it, and a settings file that
#: can't be read is off, whatever this says.
DEFAULT_ON = True
EXTENSION = "puffpai.network"
NETWORK_LISTENER = "network"
NETWORK_PAGE_LISTENER = "network-page"
NETWORK_PACKAGE = "puffpai.network_package"       # set by the server for its one package, never from a header
_LINK_LOCAL = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))
#: Every interface, as v1's ``start:lan`` did. The gate refuses a peer that is not on the home network.
BIND_ALL = "0.0.0.0"
#: One address to listen on instead. Tests set it (conftest.py) so no Core they start, in any process, reaches her real
#: network. No ``PUFFPAI_`` prefix: tests take those out of the environment of the Cores they start.
BIND_ENV = "BUILDBOT_NETWORK_BIND"
#: The addresses and plain names her browser really uses for this bot, comma-separated (``192.168.1.50,orin.local``). Set
#: it when the bot can't see them itself, as in a container with published ports. Same reason for no ``PUFFPAI_`` prefix.
ADDRESSES_ENV = "BUILDBOT_NETWORK_ADDRESSES"
_PLAIN_NAME = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?")
#: A browser reads a last label like these as an IPv4 address (2130706433 is 127.0.0.1), so it is never a name she gave.
_NUMBER_NAME = re.compile(r"[0-9]+|0x[0-9a-f]*")
NETWORK_WORKERS = 16                              # a phone holds one for each event stream
#: The biggest request the network listeners take (D134): a file sent to a package as base64 (64 MB, ``send_file``).
#: Waitress holds a body until it is whole, so a route can't stop one early. A bigger declared size is refused from its
#: headers, and a body with no declared size stops at this.
MAX_REQUEST_BYTES = 86 * 1024 * 1024
PAGE_WORKERS = 4
#: How long a looked-up list of this computer's addresses is used. A Host that misses looks again at once, at most
#: this often, so a new address from DHCP works on her next try and a wrong Host can't make the Core keep looking.
ADDRESSES_TTL_S = 30.0
ADDRESSES_MISS_S = 5.0

#: On her screen. Plain words, one fact each: developer docs may say TLS, this never does.
WORTH_KNOWING = ("A paired device can do everything you can do on the computer BuildBot runs on, your vault, keys and "
                 "command line included. Anyone on your Wi-Fi who can watch traffic could see what you type here, so use "
                 "a network you trust. They could also act as this device.")
ON_COMPUTER = "This opens only on the computer running your bot."
#: What a browser shows when it is turned away before any route: plain text (:data:`REFUSAL_TYPE`), no JSON to read.
REFUSAL_TYPE = "text/plain; charset=utf-8"
CLOSED_WORDS = b"BuildBot is closed to your home network right now."
PEER_WORDS = (b"BuildBot answers only devices on its own home network. This connection came from somewhere else, "
              b"such as a VPN or the internet, so it was turned away.")
HOST_WORDS = (b"BuildBot answers only at its own home-network addresses and its own name, on its own port. This address "
              b"is not one of them. A VPN address is not one either.")
KEYS_REFUSED = ("The home network takes only a paired browser's login. Owner keys, package tokens and Toolsmith "
                "tokens work only on this computer.")

def network_ports(core_port: int) -> tuple[int, range]:
    """THE one place the network's ports come from: ``(network listener, page range)``.

    The network listener is the bot's Core port + 3 (8423, 8433, ...), derived, never saved. Bots are 10 ports apart
    from 8420 (+0 Core, +1 proxy, +2 pages), so +3 is free in every set. Packages and tries use 18420-18999, so a bot's
    100 page ports start at 19100. When D129 lands, *core_port* comes from ``botports.read(state)["port"]``."""
    slot = max(0, int(core_port) - 8420) // 10 % 200
    base = 19100 + slot * 100
    return int(core_port) + 3, range(base, base + 100)


def peer_allowed(address: str) -> bool:
    """True only for a home-network or link-local address: never public, never loopback, never unreadable."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return any(ip in net for net in (*HOME_NETWORKS, *_LINK_LOCAL) if net.version == ip.version)


def _interface_addresses() -> list[str]:
    """Linux and other Unix: each interface's IPv4 address (Debian's hosts file gives the computer's name 127.0.1.1,
    so the name alone finds nothing there). Only the standard library. Windows has no ``fcntl``: the name finds them."""
    try:
        import fcntl
        import struct
    except ImportError:
        return []
    out: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _, name in socket.if_nameindex():
                try:
                    packed = struct.pack("256s", name.encode("utf-8")[:15])
                    out.append(socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24]))   # SIOCGIFADDR
                except OSError:       # an interface with no IPv4 address
                    continue
    except OSError:                   # no interface list here: the other two lookups still answer
        return out
    return out


def _route_address() -> list[str]:
    """The address this computer would use to reach the network. A UDP connect only picks a route: no packet is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 9))
            return [s.getsockname()[0]]
    except OSError:
        return []


def _named_addresses() -> list[str]:
    """What this computer's own name resolves to. On Windows that is every interface."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        return []


def _given() -> tuple[set[str], set[str]] | None:
    """:data:`ADDRESSES_ENV`, checked: ``(addresses, names)``, or None when nothing valid is left, which counts as unset.

    An entry is an IPv4 address that passes :func:`peer_allowed`, or ``<name>`` or ``<name>.local`` with one label, the
    shape :func:`own_names` gives. Anything else is dropped: a public address, loopback, a wildcard, an IPv6 form, an
    address with a zone or a port, a number that means an address, ``localhost``, a name under any other domain. So a
    website's own name can never be listed, and the DNS rebinding rule holds for every name not given."""
    addresses: set[str] = set()
    names: set[str] = set()
    for entry in os.environ.get(ADDRESSES_ENV, "").split(","):
        entry = entry.strip().lower()
        label = entry.removesuffix(".local")
        try:
            ipaddress.IPv4Address(entry)              # strict: peer_allowed alone also takes IPv6 and a %zone
        except ValueError:
            if _PLAIN_NAME.fullmatch(label) and label != "localhost" and not _NUMBER_NAME.fullmatch(label):
                names.add(entry)
        else:
            if peer_allowed(entry):
                addresses.add(entry)
    return (addresses, names) if addresses or names else None


def own_addresses(*, named: Callable[[], Iterable[str]] = _named_addresses,
                  interfaces: Callable[[], Iterable[str]] = _interface_addresses,
                  route: Callable[[], Iterable[str]] = _route_address) -> set[str]:
    """This computer's own home-network and link-local IPv4 addresses. Windows and Linux, standard library only. When
    :data:`ADDRESSES_ENV` holds anything valid, only the addresses in it, and nothing is looked up."""
    given = _given()
    if given is not None:
        return given[0]
    found: set[str] = set()
    for source in (named, interfaces, route):
        found.update(str(a) for a in source())
    return {a for a in found if peer_allowed(a) and ":" not in a}


def own_names(hostname: Callable[[], str] = socket.gethostname) -> set[str]:
    """``<hostname>`` and ``<hostname>.local``, lower case; none when the computer has no usable name of its own. When
    :data:`ADDRESSES_ENV` holds anything valid, only the names in it, which can be none."""
    given = _given()
    if given is not None:
        return given[1]
    try:
        name = str(hostname() or "").split(".", 1)[0].strip().lower()
    except OSError:
        return set()
    if name == "localhost" or not _PLAIN_NAME.fullmatch(name):
        return set()
    return {name, f"{name}.local"}


def _settings_path(state: Path | str) -> Path:
    return Path(state) / "settings" / "network.json"


def _saved(state: Path | str) -> dict:
    data = jsonfile.read(_settings_path(state), {})
    return data if isinstance(data, dict) else {}


def saved_on(state: Path | str) -> bool:
    """Her saved choice, else :data:`DEFAULT_ON`. A file that is there and can't be read is off: closed."""
    path = _settings_path(state)
    if not path.is_file():
        return DEFAULT_ON
    data = jsonfile.read(path, None)
    if not isinstance(data, dict):
        return False
    return DEFAULT_ON if "on" not in data else data["on"] is True


def save_on(state: Path | str, on: bool) -> None:
    with jsonfile.LOCK:
        jsonfile.write(_settings_path(state), {**_saved(state), "on": bool(on)})


def urls_of(port: int, addresses: Iterable[str], names: Iterable[str]) -> list[str]:
    """The addresses to type on a device. A number from DHCP can change; ``<name>.local`` follows it, where it works.
    A name she gave in :data:`ADDRESSES_ENV` shows as she gave it, with or without ``.local``."""
    given = _given() is not None
    named = sorted(n for n in names if given or n.endswith(".local"))
    return [f"http://{a}:{port}/" for a in sorted(addresses)] + [f"http://{n}:{port}/" for n in named]


def cookie_name(port: int) -> str:
    """The login cookie on the network. Cookies are not split by port, so two bots on one address need two names."""
    return f"puffpai_device_{int(port)}"


def page_cookie_name(port: int) -> str:
    """A package's page pass: one per page port, so opening one package never replaces another's."""
    return f"puffpai_page_{int(port)}"


def host_name(host: str) -> str:
    """The name part of a checked ``Host``, lower case."""
    return str(host or "").strip().lower().rpartition(":")[0]


def page_row_ok(row: Any) -> bool:
    """A package that may have a page port. Adopted apps stay on this computer (D130, X17), and a try is no row."""
    if not isinstance(row, dict) or row.get("status") == "removed" or adopted_origin(row.get("origin")):
        return False
    ui = row.get("ui") if isinstance(row.get("ui"), dict) else {}
    return bool(ui.get("pages") or ui.get("widgets") or ui.get("tray") or row.get("kind") == "avatar")


class PairLimit:
    """Wrong pairing codes on the network: 5 per device address in 5 minutes, and 30 from all of them together. A code
    is 60 random bits, so this is not what keeps it safe; it stops anything hammering the route. While limited, even a
    right code waits. Pairing on this computer is never limited, so a guest hammering this can't lock the owner out."""

    PER_PEER = 5
    IN_ALL = 30
    WINDOW_S = 300.0

    def __init__(self) -> None:
        self._wrong: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _tidy(self, now: float) -> None:
        for peer in list(self._wrong):
            kept = [t for t in self._wrong[peer] if now - t < self.WINDOW_S]
            if kept:
                self._wrong[peer] = kept
            else:
                del self._wrong[peer]

    def take(self, peer: str, now: float | None = None) -> bool:
        """One try, counted as wrong before the code is checked, so a burst can't all pass at once and the check itself
        holds no lock. False when limited. A right code gives it back (:meth:`forgive`)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._tidy(now)
            if len(self._wrong.get(peer, ())) >= self.PER_PEER or sum(len(v) for v in self._wrong.values()) >= self.IN_ALL:
                return False
            self._wrong.setdefault(peer, []).append(now)
            return True

    def forgive(self, peer: str) -> None:
        with self._lock:
            tries = self._wrong.get(peer)
            if tries:
                tries.pop()


class Network:
    """The listeners, this one bot's. The checks read their parts from here, so a test can stand in for the real
    network: a test binds 127.0.0.2, never every interface and never this computer's real address."""

    def __init__(self, app: Any, state: Path | str, core_port: int) -> None:
        self.app = app
        self.state = Path(state)
        self.port, self.page_range = network_ports(core_port)
        self.bind_host = os.environ.get(BIND_ENV) or BIND_ALL
        self.peer_ok: Callable[[str], bool] = peer_allowed
        self.addresses: Callable[[], Iterable[str]] = own_addresses
        self.names: Callable[[], Iterable[str]] = own_names
        self.limit = PairLimit()
        self.accepting = False              # the Core has started (``start_core``): the setting may open a socket
        self.why = ""                       # one plain line, when it is on and can't open
        self._lock = threading.RLock()
        self._server: Any = None
        self._pages: dict[str, Any] = {}
        self._threads: dict[Any, threading.Thread] = {}
        self._known: tuple[float, set[str], set[str]] = (float("-inf"), set(), set())
        self._looked = float("-inf")

    # -- the checks before any route -----------------------------------------------------------------------------
    def _look(self) -> tuple[set[str], set[str]]:
        now = time.monotonic()
        self._known = (now, {str(a) for a in self.addresses()}, {str(n).lower() for n in self.names()})
        self._looked = now
        return self._known[1], self._known[2]

    def host_ok(self, host: str, bound: str) -> bool:
        """``<own address or own name>:<this listener's port>``, exactly. Only an IPv4 address or a plain name. In a
        container, publish the same port number on the host (``-p 8433:8433``): any other number is refused here."""
        name, sep, port = str(host or "").strip().lower().rpartition(":")
        if not sep or not port or port != str(bound) or not name:
            return False
        when, addresses, names = self._known
        now = time.monotonic()
        if now - when >= ADDRESSES_TTL_S:
            addresses, names = self._look()
        elif name not in addresses and name not in names and now - self._looked >= ADDRESSES_MISS_S:
            addresses, names = self._look()
        return name in addresses or name in names

    def refuse(self, environ: dict) -> tuple[str, bytes] | None:
        """The status and body to refuse this connection with, or None. Before any route; only the socket's own peer."""
        if not saved_on(self.state):
            return "403 FORBIDDEN", CLOSED_WORDS
        if not self.peer_ok(environ.get("REMOTE_ADDR") or ""):
            return "403 FORBIDDEN", PEER_WORDS
        bound = self.port if environ.get("puffpai.listener") == NETWORK_LISTENER else environ.get("SERVER_PORT")
        if not self.host_ok(environ.get("HTTP_HOST") or "", bound or ""):
            return "421 MISDIRECTED REQUEST", HOST_WORDS
        return None

    # -- origins, from a Host already checked --------------------------------------------------------------------
    def desktop_origin(self, host: str) -> str:
        return f"http://{host_name(host)}:{self.port}"

    def page_ports(self) -> dict[str, int]:
        """Every package that may have a page, with its port, saved. A port is one package's for good: another
        package on it would inherit its origin's saved data (D53). None left: that package stays on this computer."""
        core = self.app.extensions["puffpai"]
        rows = [r for r in core.registry.list() if page_row_ok(r)]
        with jsonfile.LOCK:
            saved = _saved(self.state)
            pages = saved.get("pages") if isinstance(saved.get("pages"), dict) else {}
            taken = {p for p in pages.values() if isinstance(p, int)}
            changed = False
            for row in rows:
                if not isinstance(pages.get(row["id"]), int):
                    free = next((p for p in self.page_range if p not in taken), None)
                    if free is None:
                        continue
                    pages[row["id"]], changed = free, True
                    taken.add(free)
            if changed:
                jsonfile.write(_settings_path(self.state), {**saved, "pages": pages})
        return {r["id"]: pages[r["id"]] for r in rows if isinstance(pages.get(r["id"]), int)}

    def page_port(self, package_id: str) -> int | None:
        """A package's saved page port, or None. Never allocates: an unknown id or a removed package has none."""
        core = self.app.extensions["puffpai"]
        if not page_row_ok(core.registry.get(package_id)):
            return None
        pages = _saved(self.state).get("pages")
        port = pages.get(package_id) if isinstance(pages, dict) else None
        return port if isinstance(port, int) else None

    def page_origin(self, host: str, package_id: str) -> str | None:
        port = self.page_port(package_id)
        return f"http://{host_name(host)}:{port}" if port else None

    def page_origins(self, host: str) -> list[str]:
        """Exactly the page origins the Desktop may load, for its CSP: this name, these ports, no wildcard."""
        name = host_name(host)
        pages = _saved(self.state).get("pages")
        ports = sorted(p for p in pages.values() if isinstance(p, int)) if isinstance(pages, dict) else []
        return [f"http://{name}:{p}" for p in ports]

    # -- the listeners -------------------------------------------------------------------------------------------
    def _serve(self, sock: socket.socket, listener: str, package: str | None, threads: int) -> Any:
        from waitress import create_server

        from .app import stamp
        stamped = stamp(self.app, listener)

        def wsgi(environ, start_response):
            environ[NETWORK_PACKAGE] = package        # set by the server for its own socket
            return stamped(environ, start_response)
        server = create_server(wsgi, sockets=[sock], threads=threads, ident="puffpai-network",
                               max_request_body_size=MAX_REQUEST_BYTES)
        thread = threading.Thread(target=server.run, name=f"puffpai-{listener}-{package or 'desktop'}", daemon=True)
        self._threads[server] = thread
        thread.start()
        return server

    def open(self) -> bool:
        """Open the network listener when the setting is on and the Core is running. A port in use is one plain line."""
        from ..corelock import exclusive_socket
        with self._lock:
            if self._server is not None:
                return True
            self.why = ""
            if not self.accepting or not saved_on(self.state):
                return False
            try:
                sock = exclusive_socket(self.bind_host, self.port)
            except OSError:
                self.why = (f"Another program is using port {self.port}, so paired devices can't reach BuildBot on your "
                            "home network right now. This computer still can. Close that program and start BuildBot again.")
                return False
            self._server = self._serve(sock, NETWORK_LISTENER, None, NETWORK_WORKERS)
            return True

    def open_pages(self) -> dict[str, int]:
        """The page listeners, opened as a paired device asks. A port in use moves that package to the next free one."""
        from ..corelock import exclusive_socket
        ports = self.page_ports()
        with self._lock:
            for package in [p for p in self._pages if p not in ports]:   # removed since: its listener goes
                self._close([self._pages.pop(package)])
            if self._server is None:
                return {}
            out: dict[str, int] = {}
            for package, port in ports.items():
                if package in self._pages:
                    out[package] = port
                    continue
                try:
                    sock = exclusive_socket(self.bind_host, port)
                except OSError:
                    port, sock = self._next_free(package, port)
                if sock is None:
                    continue
                self._pages[package] = self._serve(sock, NETWORK_PAGE_LISTENER, package, PAGE_WORKERS)
                out[package] = port
            return out

    def _next_free(self, package: str, was: int) -> tuple[int | None, socket.socket | None]:
        """Another program holds a package's port: the next one no package ever had, bound and saved."""
        from ..corelock import exclusive_socket
        with jsonfile.LOCK:
            saved = _saved(self.state)
            pages = saved.get("pages") if isinstance(saved.get("pages"), dict) else {}
            taken = {p for p in pages.values() if isinstance(p, int)} | {was}
            for port in self.page_range:
                if port in taken:
                    continue
                try:
                    sock = exclusive_socket(self.bind_host, port)
                except OSError:
                    continue
                pages[package] = port
                jsonfile.write(_settings_path(self.state), {**saved, "pages": pages})
                return port, sock
        return None, None

    def _close(self, servers: list) -> None:
        from .app import stop_servers
        busy = stop_servers(servers)
        for server in servers:
            thread = self._threads.pop(server, None)
            if thread is not None and server not in busy:
                thread.join(timeout=5)

    def close(self) -> None:
        """Close every socket of this one. A request already running ends by itself (an event stream, within 25 s),
        and the check before every route refuses anything new on a connection left open."""
        with self._lock:
            servers = ([self._server] if self._server is not None else []) + list(self._pages.values())
            self._server, self._pages = None, {}
        if servers:
            self._close(servers)

    def apply(self) -> bool:
        """The saved setting, now."""
        if saved_on(self.state):
            return self.open()
        self.close()
        return False

    def is_open(self) -> bool:
        return self._server is not None

    def view(self) -> dict:
        """For Settings and ``puffpai network``. The words say what opens, and what someone watching the network could see."""
        addresses, names = self._look()
        return {"on": saved_on(self.state), "open": self.is_open(), "port": self.port, "line": self.why,
                "urls": urls_of(self.port, addresses, names), "worth_knowing": WORTH_KNOWING}
