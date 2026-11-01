"""Shared helpers for the Zone 1 tests (not a test file)."""

from __future__ import annotations

import secrets

from conftest import FakeMachine
from puffpai_core.bus import Bus
from puffpai_core.zone1.archivist import Archivist

PIN = "Tq7#mz2Lp!"          # a made-up test PIN, nobody's real one


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def canary_key() -> str:
    """A fresh key-shaped canary for one test."""
    return "sk-ant-api03-" + secrets.token_hex(20)


def make(state, *, clock=None, open_=True):
    """``(archivist, bus, events, recovery_code)``. The vault is set up (and open unless open_=False)."""
    bus = Bus()
    events: list[dict] = []
    bus.subscribe("*", events.append, audience="core")
    a = Archivist(state, bus=bus, clock=clock or FakeClock(), machine=FakeMachine())
    code = a.setup(PIN)
    if not open_:
        a.lock()
    return a, bus, events, code


def every_byte_under(path) -> bytes:
    out = b""
    for p in sorted(path.rglob("*")):
        if p.is_file():
            out += p.read_bytes()
    return out
