r"""Which ports this bot uses (D129): ``settings\ports.json`` in its own folder, so two bots can run on one computer.

``{"port": 8430, "pages_port": 8432, "proxy_port": 8431}``: the Core and Desktop, the pages address (D49) and the Bridge
proxy. Plain JSON, the same on every system, so any launcher can read it.

A bot picks once, the first time it starts (:func:`ensure`, from ``puffpai serve`` and ``puffpai setup``), and keeps them,
since the window's sign-in and a browser's are kept per address. A first bot keeps 8420, 8421 and 8422 if they are free.
Otherwise it takes the next free set, skipping what another bot in the same parent folder has saved, because a stopped
bot's ports look free. A bot folder with no file yet counts as holding today's ports: it was made before this file.

:func:`read` never writes and never asks the system: no file reads as today's ports.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from . import jsonfile

FILE = ("settings", "ports.json")
HOST = "127.0.0.1"
DEFAULT = {"port": 8420, "pages_port": 8422, "proxy_port": 8421}
#: The sets it tries after today's: 8430-8432, 8440-8442, ... below the packages' range (18420-18999).
STEP, SETS = 10, 50


def _path(state: Path | str) -> Path:
    return Path(state).joinpath(*FILE)


def _clean(data: object) -> dict | None:
    """Three different usable ports, or None."""
    if not isinstance(data, dict):
        return None
    got = {k: data.get(k) for k in DEFAULT}
    if not all(type(v) is int and 1024 <= v <= 65535 for v in got.values()) or len(set(got.values())) != len(got):
        return None
    return got


def saved(state: Path | str) -> dict | None:
    """This bot's saved ports, or None when it has none (or they can't be used)."""
    try:
        return _clean(jsonfile.read(_path(state), None))
    except (OSError, ValueError):
        return None


def read(state: Path | str) -> dict:
    return saved(state) or dict(DEFAULT)


def _free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name == "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def _claimed(state: Path | str) -> set[int]:
    """The ports other bots in the same parent folder hold, running or not."""
    here = Path(state)
    out: set[int] = set()
    try:
        others = [d for d in here.parent.iterdir() if d.is_dir() and d.name != here.name and (d / "settings").is_dir()]
    except OSError:
        return out
    for other in others:
        out |= set(read(other).values())
    return out


def _candidates():
    yield dict(DEFAULT)
    for n in range(1, SETS + 1):
        base = DEFAULT["port"] + n * STEP
        yield {"port": base, "pages_port": base + 2, "proxy_port": base + 1}


def ensure(state: Path | str) -> tuple[dict, bool]:
    """This bot's ports, picked and saved the first time. (ports, picked just now). With none free: today's, and the
    Core says plainly which port is in use."""
    have = saved(state)
    if have is not None:
        return have, False
    claimed = _claimed(state)
    for ports in _candidates():
        if not claimed & set(ports.values()) and all(_free(p) for p in ports.values()):
            try:
                jsonfile.write(_path(state), ports)
            except OSError:                      # the bot folder can't be written: the Core says so itself
                pass
            return ports, True
    return dict(DEFAULT), False


def picked_line(ports: dict, state: Path | str) -> str:
    """Said once, when a bot does not get today's ports."""
    return (f"Another bot or program uses BuildBot's usual ports, so this bot uses {ports['port']}, {ports['proxy_port']} "
            f"and {ports['pages_port']}. They are saved in {_path(state)}.")


__all__ = ["DEFAULT", "FILE", "ensure", "picked_line", "read", "saved"]
