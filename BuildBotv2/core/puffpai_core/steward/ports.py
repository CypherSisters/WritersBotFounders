# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted): RESERVED_PORTS, reserved_ports, port_in_use, listening_ports, _free_port, listen_hosts, network_reachable
"""Ports for packages (02 "Ports"; 10 "Ports").

A package's ``{port}`` comes from 18420-18999. It is free on 127.0.0.1 and
::1, no running program already listens on it (on any address), and it is not
on the owner's reserved list, ``settings\\ports.json`` (seeded with the
service ports v1 learned to leave alone, plus the Core's own 8420 and the
Bridge proxy's 8421). A package never takes a port something else already
holds (v1 ``ffa72638``, ``b6dcad41``).

Also here: who listens where, for Caretaker's watch (X17: a package bound to
0.0.0.0, ``::`` or a LAN address is stopped at once). ``listen_table`` reads
the computer's TCP listen table directly (Windows ``GetExtendedTcpTable``;
Linux ``/proc/net/tcp`` and ``tcp6``, joined to owners through ``/proc/<pid>/fd``,
else ``ss``; Mac ``lsof``), fast enough for every Caretaker tick. **A table that
can't be read is None, never an empty "clean" answer.**

A socket whose owner can't be seen (another user's) has pid 0 on Linux, as with
``ss``. A Mac's ``lsof`` does not list another user's sockets at all without root:
``free_port`` still bind-tests each port.

Honest limit: pid 0 counts for no package. A program that runs as the owner but
can't have its open files read (it made itself non-dumpable, or changed user)
shows as pid 0 too, so the X17 watch does not see it listen. Pid 0 is not treated
as an unreadable table: other users' services show it on any normal computer.
"""

from __future__ import annotations

import ctypes
import errno
import ipaddress
import json
import os
import random
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, NamedTuple


PORT_RANGE = range(18420, 19000)

#: Ports never given to a package: the owner's usual services (v1's list) and the Core's own.
RESERVED_PORTS = (3000, 3001, 3002, 3003, 3100, 3110, 3400, 4271, 5432, 5678, 8000, 8100,
                  11434, 11435, 8420, 8421, 8422)


class NoFreePort(RuntimeError):
    pass


def reserved_ports(state: Path | str) -> set[int]:
    """The owner's reserved list, ``<state>/settings/ports.json``; written with the defaults on first use."""
    path = Path(state) / "settings" / "ports.json"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"reserved": sorted(RESERVED_PORTS)}, indent=2), encoding="utf-8")
    ports = set(RESERVED_PORTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ports.update(int(p) for p in data.get("reserved", []) if str(p).isdigit())
    except (OSError, ValueError, AttributeError):
        pass                      # a broken file never frees a default
    return ports


def port_in_use(port: int) -> bool:
    """Anything listens on *port* here, on any address: it cannot be bound
    exclusively on 127.0.0.1, ::1, 0.0.0.0 or :: (a Docker-published port
    included), or it answers on ::1 at once (a dual-stack :: listener, which
    Windows lets a second program bind beside)."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET, "0.0.0.0"),
                         (socket.AF_INET6, "::1"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:  # a bind test here, no connection
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((host, int(port)))
        except OSError as exc:
            if family == socket.AF_INET6 and getattr(exc, "errno", 0) in (errno.EAFNOSUPPORT, 10047,
                                                                          errno.EADDRNOTAVAIL, 10049):
                continue                    # no IPv6 here
            return True
    for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:  # loopback only
                sock.settimeout(0.25)
                sock.connect((host, int(port)))
                return True
        except (OSError, ValueError, OverflowError):
            continue
    return False


_TCP_TABLE_OWNER_PID_LISTENER = 3
_AF_INET, _AF_INET6 = 2, 23


def _port(raw: int) -> int:
    return socket.ntohs(raw & 0xFFFF)


def _win_listen_table() -> list[tuple[int, str, int]]:
    """``(pid, address, port)`` for every TCP listen socket (IPv4 and IPv6). Raises OSError."""
    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    out: list[tuple[int, str, int]] = []
    for family in (_AF_INET, _AF_INET6):
        size = ctypes.c_ulong(0)
        iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_LISTENER, 0)
        for _ in range(4):
            buf = ctypes.create_string_buffer(size.value + 4096)
            size = ctypes.c_ulong(len(buf))
            rc = iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), False, family,
                                              _TCP_TABLE_OWNER_PID_LISTENER, 0)
            if rc == 0:
                break
            if rc != 122:                          # ERROR_INSUFFICIENT_BUFFER: grow and try again
                raise OSError(rc, "GetExtendedTcpTable failed")
        else:
            raise OSError(122, "GetExtendedTcpTable kept growing")
        raw = buf.raw
        count = int.from_bytes(raw[0:4], "little")
        row = 24 if family == _AF_INET else 56
        if 4 + count * row > len(raw):
            raise OSError(0, "GetExtendedTcpTable gave a short table")
        for i in range(count):
            r = raw[4 + i * row: 4 + (i + 1) * row]
            if family == _AF_INET:
                addr, lport, pid = socket.inet_ntoa(r[4:8]), int.from_bytes(r[8:12], "little"), \
                    int.from_bytes(r[20:24], "little")
            else:
                addr, lport, pid = str(ipaddress.IPv6Address(r[0:16])), int.from_bytes(r[20:24], "little"), \
                    int.from_bytes(r[52:56], "little")
            out.append((pid, addr, _port(lport)))
    return out


def _ss_listen_table() -> list[tuple[int, str, int]] | None:
    for ss in ("/usr/bin/ss", "/bin/ss", "/usr/sbin/ss", "/sbin/ss"):
        if os.path.isfile(ss):
            break
    else:
        return None
    try:
        got = subprocess.run([ss, "-H", "-ltnp"], capture_output=True, text=True, timeout=10, check=False,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if got.returncode != 0:
        return None
    out = []
    for line in got.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        addr, _, port = parts[3].rpartition(":")
        if not port.isdigit():
            continue
        for pid in re.findall(r"pid=(\d+)", line) or ["0"]:
            out.append((int(pid), addr.strip("[]").split("%", 1)[0], int(port)))
    return out


class TcpRow(NamedTuple):
    """One TCP socket on Linux or Mac, with the same fields and state names as ``zone3.bypass.Conn``. One row
    per process that holds it; pid 0 when its owner can't be seen."""
    pid: int
    local: str
    local_port: int
    remote: str
    remote_port: int
    state: str


#: The numbers /proc/net/tcp gives a state. 0C is a connection still arriving.
_PROC_STATES = {"01": "established", "02": "syn_sent", "03": "syn_rcvd", "04": "fin_wait1", "05": "fin_wait2",
                "06": "time_wait", "07": "closed", "08": "close_wait", "09": "last_ack", "0A": "listen",
                "0B": "closing", "0C": "syn_rcvd"}
_LSOF = "/usr/sbin/lsof"
_LSOF_STATES = {"FIN_WAIT_1": "fin_wait1", "FIN_WAIT_2": "fin_wait2", "SYN_RCDV": "syn_rcvd", "IDLE": "closed",
                "BOUND": "closed"}


def _shown(ip: Any) -> str:
    """An address in the form the watches read: an IPv4-mapped one keeps its dots (as ``ss`` writes it)."""
    mapped = getattr(ip, "ipv4_mapped", None)
    return f"::ffff:{mapped}" if mapped else str(ip)


def _hex_address(text: str) -> str:
    """An address as /proc/net/tcp writes it: 32-bit words in hex, each in this computer's byte order."""
    raw = bytes.fromhex(text)
    if len(raw) not in (4, 16):
        raise ValueError("not an address")
    if sys.byteorder == "little":
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
    return _shown(ipaddress.ip_address(raw))


def _hex_end(text: str) -> tuple[str, int]:
    addr, _, port = text.partition(":")
    return _hex_address(addr), int(port, 16)


def _parse_proc_tcp(text: str, listen_only: bool = False) -> list[tuple[int, str, int, str, int, str]]:
    """``(inode, local, local port, remote, remote port, state)`` from one /proc/net/tcp or tcp6. Raises
    ``ValueError`` on a row it can't read: a table read in part is not a table."""
    rows = []
    for line in text.splitlines()[1:]:
        f = line.split()
        if not f:
            continue
        if len(f) < 10:
            raise ValueError("a short row")
        code = f[3].upper()
        if listen_only and code != "0A":
            continue
        local, lport = _hex_end(f[1])
        remote, rport = _hex_end(f[2])
        rows.append((int(f[9]), local, lport, remote, rport, _PROC_STATES.get(code, code)))
    return rows


def _socket_owners(inodes: set[int]) -> dict[int, set[int]]:
    """Which processes hold each socket in *inodes*, from ``/proc/<pid>/fd`` (another user's can't be read)."""
    owners: dict[int, set[int]] = {}
    if not inodes:
        return owners
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with os.scandir(f"/proc/{name}/fd") as fds:
                for fd in fds:
                    try:
                        link = os.readlink(fd.path)
                    except OSError:
                        continue
                    if link.startswith("socket:["):
                        inode = int(link[8:-1])
                        if inode in inodes:
                            owners.setdefault(inode, set()).add(int(name))
        except OSError:
            continue
    return owners


def _proc_tcp_rows(listen_only: bool) -> list[TcpRow]:
    found = []
    for name in ("tcp", "tcp6"):
        try:
            with open(f"/proc/net/{name}", encoding="ascii") as fh:
                text = fh.read()
        except FileNotFoundError:
            if name == "tcp6":
                continue                             # no IPv6 here
            raise
        found += _parse_proc_tcp(text, listen_only)
    owners = _socket_owners({row[0] for row in found if row[0]})
    return [TcpRow(pid, *row[1:]) for row in found for pid in sorted(owners.get(row[0]) or (0,))]


def _lsof_end(text: str) -> tuple[str, int] | None:
    host, _, port = text.rpartition(":")
    if not port.isdigit():
        return None
    host = host.strip("[]").split("%", 1)[0]
    if host == "*":
        host = "0.0.0.0"                             # any address, of either kind: beyond this computer both ways
    try:
        return _shown(ipaddress.ip_address(host)), int(port)
    except ValueError:
        return None


def _parse_lsof(text: str) -> list[TcpRow]:
    """The lines of ``lsof -F pnT``: ``p`` a process, then for each of its sockets ``n`` its addresses
    (``local->remote``) and ``TST=`` its state. A socket with no port yet is left out."""
    rows: list[TcpRow] = []
    pid, name, state = 0, "", ""

    def flush() -> None:
        nonlocal name, state
        if name:
            local, _, remote = name.partition("->")
            here = _lsof_end(local)
            there = _lsof_end(remote) if remote else ("0.0.0.0", 0)
            if here and there:
                word = _LSOF_STATES.get(state) or state.lower() or "closed"
                rows.append(TcpRow(pid, here[0], here[1], there[0], there[1], word))
        name, state = "", ""

    for line in text.splitlines():
        kind, value = line[:1], line[1:]
        if kind == "p":
            flush()
            pid = int(value)
        elif kind == "f":
            flush()
        elif kind == "n":
            if name:
                flush()
            name = value
        elif kind == "T" and value.startswith("ST="):
            state = value[3:]
    flush()
    return rows


def _lsof_rows(listen_only: bool) -> list[TcpRow]:
    argv = [_LSOF, "-nP", "-iTCP", *(["-sTCP:LISTEN"] if listen_only else []), "-FpnT"]
    try:
        got = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False,
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise OSError(0, "lsof did not answer") from exc
    if got.returncode != 0 and (got.returncode != 1 or got.stderr.strip()):      # a bare 1: nothing matched
        raise OSError(got.returncode, "lsof could not read the table")
    return _parse_lsof(got.stdout)


def has_tcp_table() -> bool:
    """Does this kind of computer have a TCP table the Core knows how to read?"""
    return sys.platform in ("win32", "darwin") or sys.platform.startswith("linux")


def tcp_rows(listen_only: bool = False) -> list[TcpRow]:
    """Linux and Mac: every TCP socket here with its owner. Raises ``OSError`` or ``ValueError`` when the table
    can't be read."""
    return _lsof_rows(listen_only) if sys.platform == "darwin" else _proc_tcp_rows(listen_only)


def listen_table() -> list[tuple[int, str, int]] | None:
    """``(pid, address, port)`` for every TCP listen socket here, or None when it can't be read."""
    if os.name == "nt":
        try:
            return _win_listen_table()
        except (OSError, AttributeError, ValueError):
            return None
    try:
        return [(row.pid, row.local, row.local_port) for row in tcp_rows(listen_only=True)]
    except (OSError, ValueError):
        return _ss_listen_table()


def listening_ports() -> set[int]:
    """Every TCP port something listens on here, on any address. Empty when it cannot be read
    (``free_port`` also bind-tests each port, so an unread table never hands out a held port)."""
    table = listen_table()
    return {port for _pid, _addr, port in table} if table else set()


def free_port(state: Path | str, avoid: Iterable[int] = (), prefer: int | None = None) -> int:
    """A port from 18420-18999 for a package. *prefer* (its last port) wins when still free."""
    taken = listening_ports() | reserved_ports(state) | set(avoid)
    if prefer in PORT_RANGE and prefer not in taken and not port_in_use(prefer):
        return int(prefer)
    choices = [p for p in PORT_RANGE if p not in taken]
    random.shuffle(choices)
    for port in choices[:200]:
        if not port_in_use(port):
            return port
    raise NoFreePort("No free port is left between 18420 and 18999 for this package.")


def job_listeners(pids: Iterable[int]) -> list[tuple[str, int]] | None:
    """``(address, port)`` for every TCP listen socket owned by one of *pids*.

    None when the listen table can't be read: unknown, never "listens nowhere"."""
    pids = {int(p) for p in pids}
    if not pids:
        return []
    table = listen_table()
    if table is None:
        return None
    found: list[tuple[str, int]] = []
    for pid, addr, port in table:
        item = (addr.strip("[]").split("%", 1)[0], port)
        if pid in pids and item not in found:
            found.append(item)
    return found


def network_reachable(hosts: Any) -> bool:
    """Any of *hosts* answers beyond this computer (0.0.0.0, ::, *, a LAN address)."""
    for h in hosts or ():
        h = str(h).strip("[]").lower()
        if h.startswith("::ffff:"):
            h = h[7:]
        if not (h.startswith("127.") or h in ("::1", "localhost")):
            return True
    return False


__all__ = ["NoFreePort", "PORT_RANGE", "RESERVED_PORTS", "TcpRow", "free_port", "has_tcp_table", "job_listeners",
           "listen_table", "listening_ports",
           "network_reachable", "port_in_use", "reserved_ports", "tcp_rows"]
