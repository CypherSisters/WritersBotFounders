# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/box/assets/egress_proxy.py (adapted: Proxy, sni_of, answer_refusal, filter_block)
"""The built-in Bridge proxy: 127.0.0.1:8421, CONNECT and plain HTTP (D10, D23, D28(8)).

Not a provider and not replaceable (F5): every package's ``HTTP_PROXY`` and
``HTTPS_PROXY`` point here, and the Docker wall's gate forwards here.

**Who is asking (identity).** One listener, one credential per running
package: ``http://<id>:<secret>@127.0.0.1:8421``. The proxy reads the
``Proxy-Authorization: Basic`` header and maps the secret (kept hashed) to
the subject (``pkg:<id>``). No or a wrong credential is ``407`` and a ledger
line (``bad_credential``). Why not one port per package: a package that
used another package's port would get that package's grants and the Core
could not tell; a secret it does not hold can't be borrowed that way. The
secret is not the package's Core token (``PUFFPAI_TOKEN``): it opens nothing
but this proxy, and Steward mints a fresh one at each start
(:meth:`Credentials.issue`). Honest: the secret is in the package's own
environment, so a program running as the owner that reads that environment
could use it, and gains only that package's grants.

**The second listener (plain Linux Docker only, D132).** A container can't reach
this computer's 127.0.0.1 on plain Linux Docker, so :meth:`Proxy.listen_also` adds
one more listener, on the same port and the same :meth:`Proxy.handle`, at the
address of Docker's bridge (``docker_wall.wall_kw`` finds it, at each Docker app's
start). 127.0.0.1 stays. Windows, Mac and Docker Desktop never get it.
What holds: it is never every address (0.0.0.0) and never a public one. Every
request on it needs a package's credential: none or a wrong one is ``407`` and a
ledger line, exactly as on 127.0.0.1, and the Bridge then decides as it does there.
A walled app can't connect to it directly, only through its gate, because the
wall's inner network has no route to it.
What does not hold: any other container on this computer on an ordinary Docker
network, and any program on this computer, can connect, so it can get ``407`` and
add ``bad_credential`` lines to the ledger. A device on the home network normally
can't reach it (nothing there routes to Docker's addresses), but Linux answers for
its own addresses on every network card, so a neighbour that adds a route by hand
can connect unless the owner's firewall stops it, and it too needs a credential.
If the owner put Docker's bridge on her home network herself, the address is on the
home network, and the credential is all that stands there. The gate's traffic to
it is plain text inside this computer. It stays up until the Core stops.

**Rules** (fail closed at every step, as v1):
- The decision is the Core's (:meth:`.bridge.Bridge.request`), per request.
- CONNECT: the target must be a DNS name (or an exact LAN grant's address),
  the proxy resolves it once, refuses private answers (unless a LAN grant),
  dials the address it vetted (no second lookup for DNS rebinding to win; a
  ``.local`` or ``.lan`` name keeps only its home-network answers, and any
  public answer refuses it, :func:`.hosts.local_answers`),
  and the first TLS record must be a ClientHello whose SNI equals the
  CONNECT host. Not TLS, or another SNI: refused.
- Plain HTTP (``GET http://host/...``): only to a grant that allows the
  ``http`` scheme on that exact port (a grant on 443 allows CONNECT only, so
  plain HTTP to 443 is refused; :func:`.hosts.default_schemes`). **One request
  per connection, parsed and policed whole:** the request line, every header
  line (no folded lines, no bare line feeds), and the body framing (one
  ``Content-Length``, or ``Transfer-Encoding: chunked`` re-read chunk by chunk;
  both, or any other transfer coding, is refused). Only that one request is
  sent upstream, on a fresh upstream connection; the reply's head is rewritten
  to ``Connection: close``; then both sides are closed. Bytes the client sent
  after the request (a second, pipelined request with another ``Host``) are
  never forwarded. ``Proxy-*`` and hop-by-hop headers are dropped. A URL with
  a user part (``http://a@b/``) is refused.
- One ledger line per connection: subject, host, port, outcome, code, byte
  counts. Never a path, a header or a body.

**What CONNECT can't see (honest).** After the ClientHello the tunnel is
encrypted end to end, so the proxy checks only the name the client asked
for: the CONNECT host and the SNI must be the same granted name, and the
address dialled is the one the proxy resolved itself. It cannot see the HTTP
``Host`` inside the tunnel, or the inner name of an Encrypted Client Hello.
So **domain fronting** is not stopped: a package granted one name on a shared
front (a CDN or cloud edge that serves many sites) can ask that front, inside
TLS, for another site it serves. The grant bounds which front it reaches, not
which tenant behind it. Grants to shared fronts are the risk; the Yes card
names the exact host, and the Public Suffix List keeps sub-host grants off
shared suffixes.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import secrets
import threading
from typing import Any, Callable
from urllib.parse import urlsplit

from . import hosts
from .ledger import Ledger

HOST = "127.0.0.1"
PORT = 8421
MAX_HEAD = 8192
MAX_REPLY_HEAD = 65536
MAX_CHUNK_LINE = 1024
_TOKEN = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_VERBS = ("GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS")
HEAD_TIMEOUT = 10
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 120
_HOP = frozenset({"connection", "keep-alive", "proxy-authorization", "proxy-authenticate", "proxy-connection",
                  "te", "trailer", "transfer-encoding", "upgrade"})


def sni_of(record: bytes) -> str | None:
    """SNI from a TLS ClientHello record, or None. Minimal, bounds-checked."""
    try:
        if len(record) < 5 or record[0] != 0x16:
            return None
        body = record[5:5 + int.from_bytes(record[3:5], "big")]
        if not body or body[0] != 0x01:
            return None
        p = 4 + 2 + 32
        p += 1 + body[p]
        p += 2 + int.from_bytes(body[p:p + 2], "big")
        p += 1 + body[p]
        end = p + 2 + int.from_bytes(body[p:p + 2], "big")
        p += 2
        while p + 4 <= end:
            etype = int.from_bytes(body[p:p + 2], "big")
            elen = int.from_bytes(body[p + 2:p + 4], "big")
            ext = body[p + 4:p + 4 + elen]
            if etype == 0:
                q = 2
                while q + 3 <= len(ext):
                    ntype, nlen = ext[q], int.from_bytes(ext[q + 1:q + 3], "big")
                    if ntype == 0:
                        return ext[q + 3:q + 3 + nlen].decode("ascii").lower().rstrip(".")
                    q += 3 + nlen
                return None
            p += 4 + elen
    except Exception:  # noqa: BLE001
        return None
    return None


def answer_refusal(addrs: list[str], lan_ok: bool = False) -> str:
    """'' when every answer is public (or, for a LAN grant, private), else the ledger code."""
    if not addrs:
        return "cannot_look_up"
    if all(hosts.is_public(a) for a in addrs):
        return ""
    if lan_ok and all(hosts.is_private_address(a) for a in addrs):
        return ""
    if all(hosts.is_loopback(a) or a in ("0.0.0.0", "::") for a in addrs):
        return "network_filter"            # what a home DNS filter answers for a blocked name
    return "inside_address"


class _Refused(Exception):
    """A request or reply the proxy will not carry."""


def parse_head(head: bytes) -> tuple[str, str, list[tuple[str, str]]] | None:
    """``(method, target, headers)`` from one request head, or None when it is not clean HTTP/1.x:
    bare line feeds, folded or nameless header lines, odd names or versions are all refused."""
    if b"\n" in head.replace(b"\r\n", b"") or b"\r" in head.replace(b"\r\n", b"") or b"\x00" in head:
        return None
    try:
        text = head.decode("latin-1")
    except ValueError:
        return None
    first, *rest = text.split("\r\n")
    parts = first.split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.1", "HTTP/1.0") or not parts[0] or not parts[1]:
        return None
    method, target = parts[0], parts[1]
    if not set(method) <= _TOKEN or not method.isupper():
        return None
    headers: list[tuple[str, str]] = []
    for line in rest:
        if not line:
            continue
        if line[0] in " \t":
            return None                          # an old folded line: refused, never joined
        name, sep, value = line.partition(":")
        if not sep or not name or not set(name) <= _TOKEN:
            return None
        headers.append((name, value.strip(" \t")))
    return method, target, headers


def body_framing(headers: list[tuple[str, str]]) -> tuple[str, int]:
    """``("none", 0)``, ``("length", n)`` or ``("chunked", 0)``. Raises ValueError when ambiguous."""
    te = [v for n, v in headers if n.lower() == "transfer-encoding"]
    cl = [v for n, v in headers if n.lower() == "content-length"]
    if te and cl:
        raise ValueError("both Content-Length and Transfer-Encoding")
    if te:
        if len(te) != 1 or te[0].strip().lower() != "chunked":
            raise ValueError("only chunked")
        return "chunked", 0
    if cl:
        values = {v.strip() for v in cl}
        if len(values) != 1:
            raise ValueError("two lengths")
        v = values.pop()
        if not v.isdigit() or len(v) > 15:
            raise ValueError("bad length")
        return "length", int(v)
    return "none", 0


async def _send_body(reader: asyncio.StreamReader, uw: asyncio.StreamWriter, framing: tuple[str, int]) -> int:
    """Forward exactly the request's own body (re-framed chunk by chunk). Returns bytes sent."""
    kind, n = framing
    sent = 0
    if kind == "length":
        left = n
        while left:
            data = await asyncio.wait_for(reader.read(min(left, 65536)), IDLE_TIMEOUT)
            if not data:
                raise _Refused("short body")
            left -= len(data)
            uw.write(data)
            sent += len(data)
            await uw.drain()
    elif kind == "chunked":
        while True:
            line = await asyncio.wait_for(reader.readuntil(b"\r\n"), IDLE_TIMEOUT)
            if len(line) > MAX_CHUNK_LINE:
                raise _Refused("chunk line")
            size_text = line[:-2].split(b";", 1)[0].strip()
            if not size_text or any(c not in b"0123456789abcdefABCDEF" for c in size_text) or len(size_text) > 12:
                raise _Refused("chunk size")
            size = int(size_text, 16)
            if size == 0:
                while True:                      # trailers are read and dropped
                    tail = await asyncio.wait_for(reader.readuntil(b"\r\n"), IDLE_TIMEOUT)
                    if len(tail) > MAX_CHUNK_LINE:
                        raise _Refused("trailer")
                    if tail == b"\r\n":
                        break
                uw.write(b"0\r\n\r\n")
                return sent + 5
            data = await asyncio.wait_for(reader.readexactly(size + 2), IDLE_TIMEOUT)
            if data[-2:] != b"\r\n":
                raise _Refused("chunk end")
            frame = b"%x\r\n" % size + data
            uw.write(frame)
            sent += len(frame)
            await uw.drain()
    return sent


async def _relay_reply(ur: asyncio.StreamReader, writer: asyncio.StreamWriter, head_only: bool) -> int:
    """Relay the upstream reply with its head rewritten to ``Connection: close``. Returns bytes relayed."""
    got = 0
    while True:
        head = await asyncio.wait_for(ur.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT * 3)
        if len(head) > MAX_REPLY_HEAD:
            raise _Refused("reply head")
        first, *rest = head.decode("latin-1").split("\r\n")
        parts = first.split(" ", 2)
        if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not parts[1].isdigit():
            raise _Refused("not HTTP")
        status = int(parts[1])
        if status == 101:
            raise _Refused("upgrade")            # no protocol switch through the Bridge
        keep = [ln for ln in rest if ln and ln.split(":", 1)[0].strip().lower()
                not in ("connection", "keep-alive", "proxy-connection")]
        out = ("\r\n".join([first] + keep + ["Connection: close", "", ""])).encode("latin-1")
        writer.write(out)
        got += len(out)
        await writer.drain()
        if 100 <= status < 200:
            continue                             # 100 Continue and friends: the real reply follows
        break
    if head_only or status in (204, 304):
        return got
    while True:
        data = await asyncio.wait_for(ur.read(65536), IDLE_TIMEOUT)
        if not data:
            return got
        got += len(data)
        writer.write(data)
        await writer.drain()


class Credentials:
    """Proxy credentials, one per subject, kept hashed. A new issue replaces the old one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_user: dict[str, tuple[str, str, str]] = {}     # user -> (sha, subject, phase)

    def issue(self, package_id: str, *, subject: str | None = None, phase: str = "run") -> str:
        """A fresh secret for *package_id*; returns the proxy URL to put in its environment."""
        secret = secrets.token_urlsafe(24)
        digest = hashlib.sha256(secret.encode()).hexdigest()
        with self._lock:
            self._by_user[package_id] = (digest, subject or f"pkg:{package_id}", phase)
        return f"http://{package_id}:{secret}@{HOST}:{PORT}"

    def set_phase(self, package_id: str, phase: str) -> None:
        with self._lock:
            if package_id in self._by_user:
                d, s, _ = self._by_user[package_id]
                self._by_user[package_id] = (d, s, phase)

    def revoke(self, package_id: str) -> None:
        with self._lock:
            self._by_user.pop(package_id, None)

    def check(self, header: str | None) -> tuple[str, str] | None:
        """``(subject, phase)`` for a ``Proxy-Authorization`` value, or None."""
        if not header or not header[:6].lower() == "basic ":
            return None
        try:
            user, _, secret = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
        except Exception:  # noqa: BLE001
            return None
        with self._lock:
            hit = self._by_user.get(user)
        if hit is None or not hmac.compare_digest(hit[0], hashlib.sha256(secret.encode()).hexdigest()):
            return None
        return hit[1], hit[2]


def proxy_env(url: str, no_proxy: str = "127.0.0.1,localhost,::1") -> dict[str, str]:
    """The variables Steward sets for a native package (02, harvest ``box/jail.py`` names)."""
    env = {k: url for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                            "npm_config_proxy", "npm_config_https_proxy")}
    env.update(NODE_USE_ENV_PROXY="1", NO_PROXY=no_proxy, no_proxy=no_proxy)
    return env


class Proxy:
    """The proxy server. ``decide(subject, host, port, phase, scheme, method) -> Decision``."""

    def __init__(self, decide: Callable[..., Any], ledger: Ledger, credentials: Credentials, *,
                 host: str = HOST, port: int = PORT,
                 resolve: Callable[[str, int], list[str]] | None = None,
                 lan_grant: Callable[[str, str, int], bool] | None = None) -> None:
        if host not in ("127.0.0.1", "::1"):
            raise ValueError("The Bridge proxy listens on this computer only.")
        self.decide = decide
        self.ledger = ledger
        self.credentials = credentials
        self.host, self.port = host, port
        self._resolve = resolve
        # An address or .local/.lan target reaches the dial only after decide() allowed it, and decide()
        # allows one only through an exact LAN grant; this hook can narrow further.
        self.lan_grant = lan_grant or (lambda subject, host, port: True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.base_events.Server | None = None
        self._also: dict[str, asyncio.base_events.Server] = {}     # the second listener, by address
        self._also_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> int:
        """Start in a background thread; returns the bound port. Raises if it can't bind."""
        self._thread = threading.Thread(target=self._run, name="bridge-proxy", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._error is not None:
            raise self._error
        if self._server is None:
            raise RuntimeError("The Bridge proxy did not start.")
        return self.port

    def listen_also(self, address: str) -> None:
        """Also listen on *address*, same port, same checks (the module's note says what holds and what doesn't).

        One second listener at most: a new address replaces the old one. Raises ValueError for every address
        or a public one, OSError when it can't bind (the port stays only on 127.0.0.1 then), RuntimeError
        when the proxy is not running. The proxy itself keeps running in every case."""
        ip = ipaddress.ip_address(address)
        if ip.is_unspecified or not ip.is_private:
            raise ValueError("The Bridge proxy never listens on every address or on a public one.")
        loop = self._loop
        if loop is None or not loop.is_running():
            raise RuntimeError("The Bridge proxy is not running.")
        with self._also_lock:
            if address not in self._also:
                asyncio.run_coroutine_threadsafe(self._open_also(address), loop).result(10)

    async def _open_also(self, address: str) -> None:
        for old in self._also.values():
            old.close()
        self._also.clear()
        from ..corelock import exclusive_socket
        sock = exclusive_socket(address, self.port)        # no other program can share it (D91)
        self._also[address] = await asyncio.start_server(self.handle, sock=sock)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            # Bound so no other program can share the port (D91): a credential sent here reaches this proxy only.
            from ..corelock import exclusive_socket
            sock = exclusive_socket(self.host, self.port)
            self._server = loop.run_until_complete(asyncio.start_server(self.handle, sock=sock))
            self.port = self._server.sockets[0].getsockname()[1]
        except BaseException as exc:  # noqa: BLE001
            self._error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            self._server.close()
            loop.run_until_complete(self._server.wait_closed())
            for server in self._also.values():
                server.close()
            self._also.clear()
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(10)

    # -- one connection ---------------------------------------------------------
    async def _addrs(self, host: str, port: int) -> list[str]:
        if self._resolve is not None:
            return list(self._resolve(host, port))
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=1)
        return [i[4][0] for i in infos]

    def _log(self, subject: str, host: str, port: int | None, outcome: str, code: str, method: str,
             phase: str = "run", bi: int = 0, bo: int = 0) -> None:
        self.ledger.record(subject, host=host, port=port, outcome=outcome, code=code, via="proxy", phase=phase,
                           method=method, bytes_in=bi, bytes_out=bo)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except Exception:  # noqa: BLE001 - too slow, too big or not HTTP
            writer.close()
            return
        if len(head) > MAX_HEAD:
            return self._reply(writer, 431)
        parsed = parse_head(head)
        if parsed is None:
            return self._reply(writer, 400)
        method, target, headers = parsed
        method = method.upper()
        auth = next((v for n, v in headers if n.lower() == "proxy-authorization"), None)
        who = self.credentials.check(auth)
        if who is None:
            self._log("unknown", "", None, "refused", "bad_credential", method if method == "CONNECT" else "GET")
            return self._reply(writer, 407, extra=b"Proxy-Authenticate: Basic realm=\"PuffPAI Bridge\"\r\n")
        subject, phase = who
        if method == "CONNECT":
            try:
                host, port = hosts.split_hostport(target)
            except ValueError:
                self._log(subject, "", None, "refused", "not_allowed", "CONNECT", phase)
                return self._reply(writer, 400)
            await self._connect(reader, writer, subject, phase, host, port)
        else:
            await self._forward(reader, writer, subject, phase, method, target, headers)

    def _ask(self, subject: str, host: str, port: int, phase: str, scheme: str, method: str):
        return self.decide(subject=subject, host=host, port=port, phase=phase, scheme=scheme, method=method)

    async def _vetted_dial(self, subject: str, host: str, port: int, phase: str, method: str):
        """Resolve once, check every answer, dial the vetted address. (reader, writer) or None."""
        if hosts.is_ip_literal(host):
            addrs = [host]
            lan_ok = self.lan_grant(subject, host, port)
            refused = "" if (lan_ok and hosts.is_private_address(host)) else "inside_address"
        elif hosts.is_local_name(host):
            try:
                addrs = await self._addrs(host, port)
            except Exception:  # noqa: BLE001
                self._log(subject, host, port, "failed", "cannot_look_up", method, phase)
                return None
            addrs, refused = hosts.local_answers(addrs)
            if not refused and not self.lan_grant(subject, host, port):
                refused = "inside_address"
        else:
            try:
                addrs = await self._addrs(host, port)
            except Exception:  # noqa: BLE001
                self._log(subject, host, port, "failed", "cannot_look_up", method, phase)
                return None
            refused = answer_refusal(addrs)
        if refused:
            self._log(subject, host, port, "refused", refused, method, phase)
            return None
        for a in addrs:
            try:
                return await asyncio.wait_for(asyncio.open_connection(a, port), CONNECT_TIMEOUT)
            except Exception:  # noqa: BLE001
                continue
        self._log(subject, host, port, "failed", "no_answer", method, phase)
        return None

    async def _connect(self, reader, writer, subject, phase, host, port) -> None:
        d = await asyncio.get_running_loop().run_in_executor(
            None, self._ask, subject, host, port, phase, "https", "CONNECT")
        if not d.allowed:
            return self._reply(writer, 403)     # the decision's own ledger line is already written
        up = await self._vetted_dial(subject, host, port, phase, "CONNECT")
        if up is None:
            return self._reply(writer, 502)
        ur, uw = up
        counts = {"in": 0, "out": 0}
        result = ["failed", "no_answer"]
        try:
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            try:
                first = await asyncio.wait_for(reader.read(16384), HEAD_TIMEOUT)
            except Exception:  # noqa: BLE001
                first = b""
            sni = sni_of(first)
            if sni != hosts.clean(host) and not (hosts.is_ip_literal(host) and sni is None and first[:1] == b"\x16"):
                result[:] = ["refused", "not_encrypted" if not first or first[0] != 0x16 else "not_allowed"]
                return
            uw.write(first)
            counts["out"] = len(first)
            await self._pipe_both(reader, writer, ur, uw, counts)
            result[:] = ["done", ""]
        except Exception:  # noqa: BLE001
            pass
        finally:
            for w in (uw, writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
            self._log(subject, host, port, result[0], result[1], "CONNECT", phase, counts["in"], counts["out"])

    async def _forward(self, reader, writer, subject, phase, method, target, headers) -> None:
        """One plain HTTP request: police it, send only it upstream, relay the reply, close both."""
        verb = method if method in _VERBS else "GET"
        try:
            if method not in _VERBS:
                raise ValueError("method")
            u = urlsplit(target)
            if u.scheme != "http" or not u.hostname or "@" in u.netloc or "\\" in target:
                raise ValueError("not a plain absolute http URL")
            host, port = hosts.clean(u.hostname), u.port or 80
            framing = body_framing(headers)
        except ValueError:
            self._log(subject, "", None, "refused", "not_allowed", verb, phase)
            return self._reply(writer, 400)
        d = await asyncio.get_running_loop().run_in_executor(None, self._ask, subject, host, port, phase, "http", verb)
        if not d.allowed:
            return self._reply(writer, 403)
        up = await self._vetted_dial(subject, host, port, phase, verb)
        if up is None:
            return self._reply(writer, 502)
        ur, uw = up
        counts = {"in": 0, "out": 0}
        result = ["failed", "no_answer"]
        try:
            path = (u.path or "/") + (("?" + u.query) if u.query else "")
            lines = [f"{method} {path} HTTP/1.1", f"Host: {u.netloc}"]
            lines += [f"{n}: {v}" for n, v in headers
                      if n.lower() not in _HOP and n.lower() not in ("host", "content-length")]
            if framing[0] == "length":
                lines.append(f"Content-Length: {framing[1]}")
            elif framing[0] == "chunked":
                lines.append("Transfer-Encoding: chunked")
            lines += ["Connection: close", "", ""]
            out = "\r\n".join(lines).encode("latin-1")
            uw.write(out)
            counts["out"] = len(out)
            counts["out"] += await _send_body(reader, uw, framing)
            await uw.drain()
            # Only this request went upstream. Nothing more is read from the client.
            counts["in"] = await _relay_reply(ur, writer, method == "HEAD")
            result[:] = ["done", ""]
        except _Refused:
            result[:] = ["refused", "bad_answer"]
        except Exception:  # noqa: BLE001
            pass
        finally:
            for w in (uw, writer):
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass
            self._log(subject, host, port, result[0], result[1], verb, phase, counts["in"], counts["out"])

    async def _pipe_both(self, reader, writer, ur, uw, counts) -> None:
        async def pipe(src, dst, key):
            try:
                while True:
                    data = await asyncio.wait_for(src.read(65536), IDLE_TIMEOUT)
                    if not data:
                        break
                    counts[key] += len(data)
                    dst.write(data)
                    await dst.drain()
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    if dst.can_write_eof():
                        dst.write_eof()
                except Exception:  # noqa: BLE001
                    pass
        await asyncio.gather(pipe(reader, uw, "out"), pipe(ur, writer, "in"))

    @staticmethod
    def _reply(writer, status: int, extra: bytes = b"") -> None:
        words = {400: b"Bad Request", 403: b"Forbidden", 407: b"Proxy Authentication Required",
                 431: b"Request Header Fields Too Large", 502: b"Bad Gateway"}
        try:
            writer.write(b"HTTP/1.1 %d %s\r\n%sContent-Length: 0\r\nConnection: close\r\n\r\n"
                         % (status, words.get(status, b"Refused"), extra))
            writer.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = ["Credentials", "HOST", "PORT", "Proxy", "answer_refusal", "body_framing", "parse_head", "proxy_env",
           "sni_of"]
