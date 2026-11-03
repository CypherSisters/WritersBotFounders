"""Floor X2 (Z1V F14) with a live build in it (D153): the idle lock stays on, at most 60 minutes.

While a live build or an unfinished setup exists the idle timer is 60 minutes. It is never off and never reset by
asking, or by the Core's own seals. A hold that always says Yes is the worst any live-build check could do, so this is
the limit of that cost, counted from the last real use of the vault."""

import json

from puffpai_core.zone1.vault import IDLE_MAX_S
from z1kit import FakeClock, make


def test_the_idle_lock_still_fires_by_60_minutes_with_a_live_build(state):
    clock = FakeClock()
    a, _, events, _ = make(state, clock=clock)
    asked = []
    a.vault.hold = lambda: asked.append(1) or True           # a live build that never ends
    clock.advance(IDLE_MAX_S - 1)
    assert a.vault.watch_once() is False
    assert a.is_open()
    assert asked                                             # the check really was asked
    clock.advance(1)
    assert a.vault.watch_once() is True
    assert not a.is_open()
    assert [m["payload"]["reason"] for m in events if m["type"] == "vault.locked"] == ["idle"]


def test_asking_resets_nothing_and_writes_no_log_line(state):
    clock = FakeClock()
    a, _, _, _ = make(state, clock=clock)
    a.vault.hold = lambda: True
    before = a.vault.log_path.read_text(encoding="utf-8")
    touched = a.vault._last_touch
    clock.advance(301)
    for _ in range(50):
        assert a.vault.watch_once() is False                 # the idle timer's own wake, many times
        clock.advance(5)
    assert a.vault._last_touch == touched
    assert a.vault.log_path.read_text(encoding="utf-8") == before
    clock.advance(IDLE_MAX_S)
    assert not a.is_open()
    last = json.loads(a.vault.log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert (last["action"], last["reason"]) == ("lock", "idle")


def test_core_seals_during_the_hold_do_not_stretch_it(state):
    """Seals run all through a build (each outside-log and audit line). They still need an open vault, and it still locks
    at 60 minutes."""
    clock = FakeClock()
    a, _, events, _ = make(state, clock=clock)
    a.vault.hold = lambda: True
    for n in range(IDLE_MAX_S // 60 - 1):
        clock.advance(60)
        assert a.anchor_put("core.audit", {"n": n, "h": "x"}) is True
        assert a.anchor_get("core.audit") == {"n": n, "h": "x"}
    clock.advance(60)
    assert a.vault.watch_once() is True
    assert not a.is_open()
    assert [m["payload"]["reason"] for m in events if m["type"] == "vault.locked"] == ["idle"]
    assert a.anchor_put("core.audit", {"n": 99, "h": "x"}) is False
