"""Stop on bypass (D26): a native package caught connecting around the Bridge proxy is stopped at once.

How: every ``interval`` seconds the watch reads this computer's TCP table
(IPv4 and IPv6, with the owning process id: Windows ``GetExtendedTcpTable``,
Linux ``/proc/net`` joined to ``/proc/<pid>/fd``, Mac ``lsof``) and
compares it with the process ids in each running package's job
(Steward's ``pids(package_id)``, lane L1). A package process that holds a TCP
connection to anything but loopback went around the proxy (the proxy itself
is on 127.0.0.1, and the Core's own traffic is not in any package's
job). Connections that arrive at a port the same process listens on are
inbound (the owner opened the app to her home network) and do not count.

Steward (lane L1) gives exactly three calls: ``running() -> [id]``,
``pids(id) -> [int]`` (every process in the package's job, so the
real program a launcher started is watched too, not only the launcher) and
``stop(id, reason: str)``. If any of them is missing, or ``running()`` or the
TCP table fails, the watch is **not running** and says so plainly
(:meth:`BypassWatch.status`, and one card that suggests safe mode). It never
quietly watches nothing.

Then, in this order: Steward stops the package (``stop(package_id, reason)``),
one ledger line (``via: direct``, ``around_the_bridge``), ``egress.observed``
on the Bus, and a card with two buttons only: **Start again** and **Remove**.

Honest limits (said on the Bridge page): this is a poll, so a short
connection that opens and closes between two reads can slip; UDP has no
remote address in the table and is not seen; a process that left the job
(or was started outside it) is not watched; Docker packages are not
watched this way (Docker's traffic does not leave from the package's own
processes), the Docker wall holds them instead (D30). On Linux and Mac a
process that left its session after its parent died has left the job
(``steward.jobs``); a Mac's table comes from ``lsof``, a whole program run
on each pass.
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol

from ..steward import ports

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
LISTEN = 2
STATES = {1: "closed", 2: "listen", 3: "syn_sent", 4: "syn_rcvd", 5: "established", 6: "fin_wait1",
          7: "fin_wait2", 8: "close_wait", 9: "closing", 10: "last_ack", 11: "time_wait", 12: "delete_tcb"}
CARD_LINE = ("{name} reached the internet around the Bridge, so it was stopped. "
             "Start it again, or remove it.")
STOP_FAILED_LINE = ("{name} reached the internet around the Bridge. PuffPAI tried to stop it, but it did not stop. "
                    "Remove it, or restart PuffPAI in safe mode.")
NOT_WATCHING_LINE = ("PuffPAI can't watch for apps that go around the Bridge right now ({why}), so one that did "
                     "would not be stopped. Restart PuffPAI in safe mode to be sure.")
CANT_WATCH_APP_LINE = ("PuffPAI can't see the programs of {name}, so it can't tell if it goes around the Bridge. "
                       "Stop it, or restart PuffPAI in safe mode.")
STEWARD_CALLS = ("running", "pids", "stop")
WHY = {"steward_incomplete": "Steward can't list, watch or stop apps",
       "running_failed": "Steward couldn't say which apps are running",
       "no_tcp_table": "this computer wouldn't list its connections",
       "not_windows": "this kind of computer has no list of connections the watch can read"}


class Steward(Protocol):
    """What the watch needs from Steward (lane L1)."""

    def running(self) -> Iterable[str]: ...                  # package ids running now
    def pids(self, package_id: str) -> Iterable[int]: ...    # process ids in its job
    def stop(self, package_id: str, reason: str) -> Any: ...  # stop at once, everything in the job


@dataclass(frozen=True)
class Conn:
    pid: int
    local: str
    local_port: int
    remote: str
    remote_port: int
    state: str


def _port(raw: int) -> int:
    return socket.ntohs(raw & 0xFFFF)


def tcp_table() -> list[Conn]:
    """Every TCP connection on this computer with its owning process. Raises when it can't be read."""
    if os.name != "nt":
        return [Conn(*row) for row in ports.tcp_rows()]
    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    out: list[Conn] = []
    for family in (AF_INET, AF_INET6):
        size = ctypes.c_ulong(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
        for _ in range(4):
            buf = ctypes.create_string_buffer(size.value + 4096)
            size = ctypes.c_ulong(len(buf))
            rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_ALL, 0)
            if rc == 0:
                break
            if rc != 122:                    # ERROR_INSUFFICIENT_BUFFER: grow and retry
                raise OSError(rc, "GetExtendedTcpTable failed")
        else:
            raise OSError(122, "GetExtendedTcpTable kept growing")
        raw = buf.raw
        count = int.from_bytes(raw[0:4], "little")
        if family == AF_INET:
            row, base = 24, 4
            for i in range(count):
                r = raw[base + i * row: base + (i + 1) * row]
                state, laddr, lport, raddr, rport, pid = (int.from_bytes(r[j:j + 4], "little") for j in range(0, 24, 4))
                out.append(Conn(pid, socket.inet_ntoa(r[4:8]), _port(lport), socket.inet_ntoa(r[12:16]),
                                _port(rport), STATES.get(state, str(state))))
        else:
            row, base = 56, 4
            for i in range(count):
                r = raw[base + i * row: base + (i + 1) * row]
                laddr, rem = r[0:16], r[24:40]
                lport = int.from_bytes(r[20:24], "little")
                rport = int.from_bytes(r[44:48], "little")
                state = int.from_bytes(r[48:52], "little")
                pid = int.from_bytes(r[52:56], "little")
                out.append(Conn(pid, str(ipaddress.IPv6Address(laddr)), _port(lport),
                                str(ipaddress.IPv6Address(rem)), _port(rport), STATES.get(state, str(state))))
    return out


def _local_only(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def find_bypass(pids: Iterable[int], conns: Iterable[Conn]) -> list[Conn]:
    """Connections owned by *pids* that reach past this computer (not via the proxy)."""
    watch = {int(p) for p in pids if int(p) > 0}
    if not watch:
        return []
    mine = [c for c in conns if c.pid in watch]
    listening = {(c.pid, c.local_port) for c in mine if c.state == "listen"}
    return [c for c in mine if c.state != "listen" and not _local_only(c.remote)
            and (c.pid, c.local_port) not in listening]


class BypassWatch:
    """The connection watch. ``check_once()`` is one pass; ``start()`` runs it on a thread."""

    def __init__(self, steward: Steward, *, ledger: Any = None, bus: Any = None, registry: Any = None,
                 on_card: Callable[[dict], None] | None = None, interval: float = 0.5,
                 table: Callable[[], list[Conn]] = tcp_table) -> None:
        self.steward = steward
        self.ledger = ledger
        self.bus = bus
        self.registry = registry
        self.on_card = on_card
        self.interval = interval
        self.table = table
        self.stopped: list[dict] = []
        self.missing = [n for n in STEWARD_CALLS if not callable(getattr(steward, n, None))]
        self.problem = "steward_incomplete" if self.missing else \
            ("not_windows" if table is tcp_table and not ports.has_tcp_table() else "")
        self.blind: set[str] = set()                 # packages whose programs it could not list
        self._told: set[str] = set()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    # -- what it says about itself ---------------------------------------------
    def status(self) -> dict:
        """``{running, reason, line, safe_mode, missing, blind}``: plain, never a quiet "all fine"."""
        watching = not self.problem and self._thread is not None and self._thread.is_alive()
        reason = self.problem or ("" if watching else "not_started")
        return {"running": watching, "reason": reason,
                "line": NOT_WATCHING_LINE.format(why=WHY.get(self.problem, "it was not started")) if reason else "",
                "safe_mode": bool(self.problem), "missing": list(self.missing), "blind": sorted(self.blind)}

    def _card_once(self, key: str, card: dict) -> None:
        if key in self._told:
            return
        self._told.add(key)
        if self.on_card is not None:
            try:
                self.on_card(card)
            except Exception:  # noqa: BLE001
                pass

    def _broken(self, problem: str) -> list[dict]:
        self.problem = problem
        self._card_once("watch:" + problem, {"kind": "watch_not_running", "reason": problem,
                                             "line": NOT_WATCHING_LINE.format(why=WHY[problem]),
                                             "buttons": ["safe_mode"]})
        return []

    def check_once(self) -> list[dict]:
        if self.problem:
            return self._broken(self.problem)
        try:
            running = list(self.steward.running())
        except Exception:  # noqa: BLE001 - said plainly, never a silent []
            return self._broken("running_failed")
        if not running:
            return []
        try:
            conns = self.table()
        except Exception:  # noqa: BLE001
            return self._broken("no_tcp_table")
        caught = []
        for pkg in running:
            try:
                pids = [int(x) for x in self.steward.pids(pkg)]
            except Exception:  # noqa: BLE001 - this one app can't be watched: said on a card
                self.blind.add(str(pkg))
                self._card_once("blind:" + str(pkg), {"kind": "watch_blind", "package": str(pkg),
                                                      "line": CANT_WATCH_APP_LINE.format(name=self._name(pkg)),
                                                      "buttons": ["stop", "safe_mode"]})
                continue
            self.blind.discard(str(pkg))
            hits = find_bypass(pids, conns)
            if hits:
                caught.append(self._stop(pkg, hits[0]))
        return caught

    def _name(self, pkg: str) -> str:
        try:
            row = self.registry.get(pkg) if self.registry is not None else None
            return (row or {}).get("name") or pkg
        except Exception:  # noqa: BLE001
            return pkg

    def _stop(self, pkg: str, hit: Conn) -> dict:
        reason = "Reached the internet around the Bridge"
        stopped = True
        try:
            self.steward.stop(pkg, reason)          # first, at once
        except Exception:  # noqa: BLE001 - recorded and carded below, with what happened
            stopped = False
        subject = f"pkg:{pkg}"
        if self.ledger is not None:
            self.ledger.record(subject, host=hit.remote, port=hit.remote_port, outcome="refused",
                               code="around_the_bridge", via="direct", method="CONNECT")
        if self.bus is not None:
            from ..bus import Bus, Source
            payload = {"subject": subject, "host": "unknown", "port": hit.remote_port, "via": "direct",
                       "outcome": "allowed", "reason_code": "around_the_bridge", "matched": None}
            if ":" not in hit.remote:
                payload["host"] = hit.remote
            self.bus.publish(Bus.message("egress.observed", payload, Source("core", "core:bridge")))
        if self.registry is not None:
            try:
                self.registry.update(pkg, status="broken")
                self.registry.set_health(pkg, False, reason + ", so it was stopped.")
            except Exception:  # noqa: BLE001
                pass
        name = self._name(pkg)
        card = {"kind": "bypass", "package": pkg, "stopped": stopped,
                "line": (CARD_LINE if stopped else STOP_FAILED_LINE).format(name=name),
                "buttons": ["start_again", "remove"] if stopped else ["remove", "safe_mode"]}
        self.stopped.append(card)
        if self.on_card is not None:
            try:
                self.on_card(card)
            except Exception:  # noqa: BLE001
                pass
        return card

    def start(self) -> None:
        """Run the watch on a thread. A Steward without the three calls: not started, said on a card."""
        if self._thread is not None:
            return
        if self.problem:
            self._broken(self.problem)
            return
        self._halt.clear()

        def loop() -> None:
            while not self._halt.wait(self.interval):
                try:
                    self.check_once()
                except Exception:  # noqa: BLE001 - one bad pass never ends the watch
                    pass
        self._thread = threading.Thread(target=loop, name="bridge-bypass-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._halt.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None


__all__ = ["BypassWatch", "CARD_LINE", "Conn", "NOT_WATCHING_LINE", "STEWARD_CALLS", "Steward", "find_bypass",
           "tcp_table"]
