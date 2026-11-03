"""D151: a live write during the unlock check must not read as a rollback, and a real rollback still closes the Bridge.

The bug: the check read the files, then the vault's copy. A build writes the outside log all the time, so the two could
come from different moments: a line on disk whose seal had not landed (a writer waits for the vault during the PIN
check, so this was likely on every unlock during a build), or a seal newer than the read. Then the Bridge closed itself.

These use the real Archivist and vault (made-up PIN, fake machine key), the real Zone3, Bridge, Policy, ChainLog and
ConsentStore, and real threads. Nothing leaves this computer: names never resolve here.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from puffpai_core.bus import Bus
from puffpai_core.consent.store import ConsentError, ConsentStore
from puffpai_core.zone1 import crypto
from puffpai_core.zone3 import vault_anchor
from puffpai_core.zone3.bridge import CORE
from puffpai_core.zone3.service import Zone3
from puffpai_core.zone3.toolsmith import agent, find, scout
from puffpai_core.zone3.toolsmith.errors import Refused
from puffpai_core.zone3.toolsmith_host import store
from puffpai_core.zone3.toolsmith_host.ports import Ports
from puffpai_core.zone3.vault_anchor import AUDIT, CANT_CONFIRM_LINE, GENERATION, LEDGER, MISMATCH_LINE, POLICY
from puffpai_core.zone3.toolsmith_host import sender
from toolsmith_fakes import FakePorts, Script, World, tool
from z1kit import PIN, make
from z3helpers import grant, manifest

ANCHORS = (LEDGER, AUDIT, POLICY, GENERATION)


class Bot:
    """A set-up vault, open, and a Zone 3 on the same Bus, as in the Core. The Bridge is open, with Yes records to take back."""

    def __init__(self, state, yeses=4):
        self.state = state
        self.a, self.bus, _events, _code = make(state)
        self.z = Zone3(state, bus=self.bus, archivist=self.a)
        self.yes = [grant(self.z, manifest(f"app{i}", hosts=[f"api.app{i}.example.com"]))["consent_id"]
                    for i in range(yeses)]
        self.n = 0

    def restart(self):
        """The Core stops and starts, vault locked. The Yes records stay on disk."""
        self.a.lock()
        self.z.stop()
        self.z = Zone3(self.state, bus=self.bus, archivist=self.a)

    def write(self, name):
        """One real write of that anchor, the way the running Core makes it."""
        self.n += 1
        n = self.n
        if name == LEDGER:
            self.z.bridge.request("pkg:app0", "api.app0.example.com")
        elif name == AUDIT:
            self.z.audit.append({"event": "test_line", "n": n})
        elif name == POLICY:
            self.z.bridge.policy.add_block(f"b{n}.example.com", "host", "test")
        else:
            assert self.z.consents.revoke(self.yes.pop())

    def seal_slot(self, name):
        """(object, attribute) of the call that hands this anchor's new value to the vault."""
        return {LEDGER: (self.z.bridge.ledger.chain, "seal"), AUDIT: (self.z.audit, "seal"),
                POLICY: (self.z.bridge.policy, "seal"), GENERATION: (self.z.consents, "on_generation")}[name]

    def files(self, name):
        logs, bridge = self.state / "logs", self.state / "settings" / "bridge"
        return {LEDGER: [logs / "outside.jsonl", logs / "outside.jsonl.head"],
                AUDIT: [logs / "audit.jsonl", logs / "audit.jsonl.head"],
                POLICY: [bridge / "policy.json", bridge / "policy.version"],
                GENERATION: [self.state / "consent.db"]}[name]

    def old_copy(self, name):
        return {p: p.read_bytes() for p in self.files(name)}

    def state_now(self):
        return self.z.bridge.posture()["state"]

    def mismatch_cards(self):
        return [c for c in self.z.cards if c["kind"] == "vault_mismatch"]

    def unlock(self):
        self.a.unlock("pin", PIN)


@pytest.fixture
def bot(state):
    return Bot(state)


def stays_open(bot):
    cards = bot.mismatch_cards()
    assert not cards, f"the Bridge closed itself on a live write: {[(c['what'], c['why']) for c in cards]}"
    assert bot.state_now() == "open"


def closes(bot, name, line=MISMATCH_LINE):
    assert bot.state_now() == "closed", f"{name}: the Bridge stayed open"
    cards = bot.mismatch_cards()
    assert cards and any(name in c["what"] for c in cards), f"{name}: no card names it: {cards}"
    assert any(c["line"] == line for c in cards), [c["line"] for c in cards]


def in_a_thread(fn):
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    return t


# -- the reproduction: a write in each dangerous spot, on a real unlock -----------------------------------------------

@pytest.mark.parametrize("name", ANCHORS)
def test_a_write_on_disk_whose_seal_has_not_landed_is_not_a_rollback(bot, name):
    """Between the append and the seal: the file is newer than expected, in any read order."""
    bot.write(name)                              # a bot that has run: the vault holds a value for every anchor
    bot.a.lock()
    bot.unlock()
    bot.a.lock()
    owner, attr = bot.seal_slot(name)
    real = getattr(owner, attr)
    entered, go = threading.Event(), threading.Event()

    def late(*args):
        entered.set()
        go.wait(10)
        return real(*args)
    setattr(owner, attr, late)
    t = in_a_thread(lambda: bot.write(name))
    assert entered.wait(10)
    real_check = bot.z.vault.unlocked

    def checks(*a, **k):                         # the seal lands only after the check has begun, whatever the PIN took
        threading.Timer(0.3, go.set).start()
        return real_check(*a, **k)
    bot.z.vault.unlocked = checks
    bot.unlock()
    t.join(10)
    assert not t.is_alive()
    stays_open(bot)


def test_a_write_during_the_real_pin_check_is_not_a_rollback(bot, monkeypatch):
    """The real shape of it: a send lands while the PIN check holds the vault, so its seal waits for the vault."""
    bot.write(LEDGER)
    bot.a.lock()
    log = bot.state / "logs" / "outside.jsonl"
    real = crypto.unwrap
    started = []

    def slow_way_in(*args, **kw):
        if not started:
            before = log.stat().st_size
            started.append(in_a_thread(lambda: bot.write(LEDGER)))
            until = time.monotonic() + 5
            while log.stat().st_size == before and time.monotonic() < until:
                time.sleep(0.005)
            assert log.stat().st_size > before
        return real(*args, **kw)
    monkeypatch.setattr(crypto, "unwrap", slow_way_in)
    bot.unlock()
    started[0].join(10)
    stays_open(bot)


@pytest.mark.parametrize("name,at", [(LEDGER, POLICY), (AUDIT, POLICY), (GENERATION, POLICY), (LEDGER, LEDGER),
                                     (AUDIT, AUDIT), (POLICY, POLICY), (GENERATION, GENERATION)])
def test_a_write_between_the_reads_or_during_anchor_get_is_not_a_rollback(bot, monkeypatch, name, at):
    """The file read, then a real write, then the vault's copy: expected is newer than the file. ``at`` is the anchor
    the check is reading from the vault when the write comes. A write of the same anchor waits for the check."""
    bot.a.lock()
    real_get = bot.a.anchor_get
    thread = []

    def get(asked):
        if asked == at and not thread:
            thread.append(in_a_thread(lambda: bot.write(name)))
            thread[0].join(0.3)
        return real_get(asked)
    monkeypatch.setattr(bot.a, "anchor_get", get)
    bot.unlock()
    assert thread
    thread[0].join(10)
    assert not thread[0].is_alive()
    stays_open(bot)


def test_a_write_after_the_compare_is_not_overwritten_by_an_older_value(bot, monkeypatch):
    """The check hands the vault what it compared. A newer seal must not be put back to that older value, or the next
    unlock reads a rollback that never happened, even with nothing else writing."""
    bot.a.lock()
    real_put = bot.a.anchor_put
    wrote = []

    def put(name, value):
        if name == POLICY and threading.current_thread() is threading.main_thread() and not wrote:
            wrote.append(in_a_thread(lambda: bot.write(LEDGER)))
            wrote[0].join(0.3)
        return real_put(name, value)
    monkeypatch.setattr(bot.a, "anchor_put", put)
    bot.unlock()
    assert wrote
    wrote[0].join(10)
    head, n = bot.z.bridge.ledger.chain.head()
    assert bot.a.anchor_get(LEDGER) == {"n": n, "h": head}, "the vault's copy went back"
    bot.a.lock()
    bot.unlock()
    stays_open(bot)


def test_opening_after_a_mismatch_does_not_put_back_an_older_value(bot, monkeypatch):
    """The owner's open after a card takes the files as they are; a live write in the middle stays the newest."""
    real = vault_anchor._log_value
    wrote = []

    def read(log):
        got = real(log)
        if not wrote and log.path.name == "outside.jsonl":
            wrote.append(in_a_thread(lambda: bot.write(LEDGER)))
            wrote[0].join(0.3)
        return got
    bot.z.vault.mismatch = True
    monkeypatch.setattr(vault_anchor, "_log_value", read)
    bot.z.bridge.open()
    wrote[0].join(10)
    monkeypatch.undo()
    head, n = bot.z.bridge.ledger.chain.head()
    assert bot.a.anchor_get(LEDGER) == {"n": n, "h": head}
    assert bot.z.vault.mismatch is False


def test_many_writes_during_many_checks(state):
    bot = Bot(state, yeses=8)
    stop = threading.Event()
    counts = dict.fromkeys(ANCHORS, 0)

    def spam(name, pause):
        while not stop.is_set():
            if name == GENERATION and not bot.yes:
                return
            bot.write(name)
            counts[name] += 1
            time.sleep(pause)
    threads = [in_a_thread(lambda n=n, p=p: spam(n, p)) for n, p in ((LEDGER, 0.0), (AUDIT, 0.0), (POLICY, 0.01), (GENERATION, 0.2))]
    try:
        for _ in range(3):
            bot.a.lock()
            time.sleep(0.05)
            bot.unlock()
            stays_open(bot)
    finally:
        stop.set()
        for t in threads:
            t.join(30)
    assert all(not t.is_alive() for t in threads)
    assert counts[LEDGER] > 3 and counts[AUDIT] > 3 and counts[POLICY] > 3 and counts[GENERATION] >= 1, counts
    bot.a.lock()                                     # quiet now: the vault's copy is still the truth
    bot.unlock()
    stays_open(bot)
    assert bot.z.vault.pending == {}
    head, n = bot.z.bridge.ledger.chain.head()
    assert bot.a.anchor_get(LEDGER) == {"n": n, "h": head}


# -- a real rollback still closes -------------------------------------------------------------------------------------

def _rolled_back(bot, name):
    """An old, genuinely sealed copy of the files, put back while the Core was stopped, the vault holding newer."""
    if name != GENERATION:
        bot.write(name)
    old = bot.old_copy(name)
    for _ in range(2):
        bot.write(name)
    bot.a.lock()
    bot.z.stop()
    for p, data in old.items():
        p.write_bytes(data)
    bot.z = Zone3(bot.state, bus=bot.bus, archivist=bot.a)
    if name == GENERATION:
        bot.yes = [r["consent_id"] for r in bot.z.consents.live() if r["scope"]["kind"] != "cloud"]


@pytest.mark.parametrize("name", ANCHORS)
def test_a_real_rollback_of_each_anchor_closes(bot, name):
    _rolled_back(bot, name)
    bot.unlock()
    closes(bot, name)


@pytest.mark.parametrize("name", ANCHORS)
def test_a_real_rollback_closes_with_other_writes_going_on(bot, name):
    _rolled_back(bot, name)
    stop = threading.Event()
    others = [n for n in (LEDGER, AUDIT, POLICY) if n != name]

    def spam(n):
        while not stop.is_set():
            bot.write(n)
            time.sleep(0.002)
    threads = [in_a_thread(lambda n=n: spam(n)) for n in others]
    time.sleep(0.05)
    try:
        bot.unlock()
    finally:
        stop.set()
        for t in threads:
            t.join(30)
    closes(bot, name)
    assert [c for c in bot.mismatch_cards() if name in c["what"]][0]["why"] == ["rolled_back"]


@pytest.mark.parametrize("name", ANCHORS)
def test_a_real_rollback_closes_with_its_own_write_during_the_check(bot, monkeypatch, name):
    """A busy writer on the rolled back file itself can't talk the check out of it: the write waits, the files are read."""
    _rolled_back(bot, name)
    real_get = bot.a.anchor_get
    thread = []

    def get(asked):
        if asked == name and not thread:
            thread.append(in_a_thread(lambda: bot.write(name)))
            thread[0].join(0.2)
        return real_get(asked)
    monkeypatch.setattr(bot.a, "anchor_get", get)
    bot.unlock()
    thread[0].join(10)
    closes(bot, name)


@pytest.mark.parametrize("name", [LEDGER, AUDIT, GENERATION])
def test_own_write_during_earlier_anchor_read(bot, monkeypatch, name):
    """Rolled back file. Its own writer's line comes while the check reads an earlier anchor: all the locks are held
    by then, so it waits, and the check reads the rolled back file against the vault's newer copy."""
    _rolled_back(bot, name)
    real_get = bot.a.anchor_get
    thread = []

    def get(asked):
        if asked == POLICY and not thread:
            thread.append(in_a_thread(lambda: bot.write(name)))
            thread[0].join(0.3)
        return real_get(asked)
    monkeypatch.setattr(bot.a, "anchor_get", get)
    bot.unlock()
    thread[0].join(10)
    assert not thread[0].is_alive()
    closes(bot, name)


@pytest.mark.parametrize("name", ANCHORS)
def test_own_write_during_inbox_filing(bot, monkeypatch, name):
    """Rolled back file. Its own line comes while the inbox is filed, which anyone can make slow: the check ran first."""
    _rolled_back(bot, name)
    real = bot.a._file_inbox
    thread = []

    def filing(*a, **k):
        if not thread:
            thread.append(in_a_thread(lambda: bot.write(name)))
            thread[0].join(5)
        return real(*a, **k)
    monkeypatch.setattr(bot.a, "_file_inbox", filing)
    bot.unlock()
    thread[0].join(10)
    closes(bot, name)


def test_the_check_runs_before_the_inbox_is_filed(bot, monkeypatch):
    order = []
    real_file, real_check = bot.a._file_inbox, bot.z.vault.unlocked

    def filing(*a, **k):
        order.append("file")
        return real_file(*a, **k)

    def checking(*a, **k):
        order.append("check")
        return real_check(*a, **k)
    monkeypatch.setattr(bot.a, "_file_inbox", filing)
    bot.z.vault.unlocked = checking
    bot.a.lock()
    bot.unlock()
    assert order == ["check", "file"]


def test_a_broken_check_still_closes_and_the_inbox_is_still_filed(bot, monkeypatch):
    filed = []
    real_file = bot.a._file_inbox

    def broken(*_a, **_k):
        raise RuntimeError("no")

    def filing(*a, **k):
        filed.append(True)
        return real_file(*a, **k)
    bot.z.vault.unlocked = broken
    monkeypatch.setattr(bot.a, "_file_inbox", filing)
    bot.a.lock()
    bot.unlock()
    assert bot.state_now() == "closed" and filed
    assert bot.a.is_open(), "the unlock itself stands"


def test_one_limit_for_all_the_locks(bot, monkeypatch):
    """Every writer stuck: one wait, not one per anchor. Nothing is read or handed to the vault."""
    monkeypatch.setattr(vault_anchor, "CHECK_WAIT_S", 0.4)
    z = bot.z
    locks = [z.bridge.policy.write_lock, z.consents.write_lock, z.bridge.ledger.chain.write_lock, z.audit.write_lock]
    bot.a.lock()
    held, end = threading.Event(), threading.Event()

    def wedged():
        for lock in locks[1:]:
            lock.acquire()
        held.set()
        end.wait(30)
        for lock in locks[1:]:
            lock.release()
    t = in_a_thread(wedged)
    assert held.wait(5)
    puts = []
    real_put = bot.a.anchor_put
    monkeypatch.setattr(bot.a, "anchor_put", lambda n, v: puts.append(n) or real_put(n, v))
    began = time.monotonic()
    try:
        got = z.vault_unlocked()                     # the vault is locked here, which does not matter: it never gets that far
    finally:
        end.set()
        t.join(10)
    assert time.monotonic() - began < 2.0
    assert got == [{"what": GENERATION, "why": "cant_confirm"}]
    assert puts in ([], [POLICY]), puts                # at most the policy reading closed, after the locks
    closes(bot, GENERATION, CANT_CONFIRM_LINE)
    assert not any(lock.locked() for lock in locks[2:]), "every lock let go"


def test_two_unlocks_at_once(bot):
    """Both checks finish and let go, with writers going. Nothing false, nothing stuck."""
    bot.write(LEDGER)
    bot.a.lock()
    stop = threading.Event()

    def spam():
        while not stop.is_set():
            bot.write(LEDGER)
            time.sleep(0.002)
    writer = in_a_thread(spam)
    unlocks = [in_a_thread(bot.unlock) for _ in range(2)]
    try:
        for t in unlocks:
            t.join(60)
        assert not any(t.is_alive() for t in unlocks)
    finally:
        stop.set()
        writer.join(30)
    assert not writer.is_alive()
    stays_open(bot)


def test_a_lock_during_the_check(bot, monkeypatch):
    """The owner locks while the check reads: no deadlock, a write waiting on it finishes, and the check can't confirm,
    so it closes. The next unlock finds the files and the kept values still agree."""
    bot.write(LEDGER)
    bot.a.lock()
    bot.unlock()
    bot.a.lock()
    real_get = bot.a.anchor_get
    threads = []

    def get(asked):
        if asked == GENERATION and not threads:
            threads.append(in_a_thread(lambda: bot.write(LEDGER)))
            threads.append(in_a_thread(lambda: bot.a.lock()))
            threads[1].join(10)
            assert not threads[1].is_alive(), "the lock did not wait for the check"
        return real_get(asked)
    monkeypatch.setattr(bot.a, "anchor_get", get)
    bot.unlock()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads)
    assert not bot.a.is_open() and bot.state_now() == "closed"
    assert all(p["why"] == "cant_confirm" for p in bot.z.vault.problems) and bot.z.vault.problems
    monkeypatch.undo()
    bot.unlock()
    assert not [p for p in bot.z.vault.problems if p["why"] == "rolled_back"], bot.z.vault.problems


def test_a_policy_that_turns_damaged_during_the_check_says_so_with_the_locks_let_go(bot):
    """Its card can start safe mode, which writes the audit log: with that log's lock held, on this thread, that would
    never return."""
    z = bot.z
    seen = []
    z.cards.listeners.append(lambda card: seen.append((card["kind"], z.audit.write_lock.locked(), z.bridge.ledger.chain.write_lock.locked())))
    bot.a.lock()
    path = bot.files(POLICY)[0]
    path.write_bytes(path.read_bytes().replace(b'"open"', b'"closed"', 1))
    bot.unlock()
    assert z.bridge.posture()["state"] == "closed"
    damaged = [s for s in seen if s[0] == "bridge_damaged"]
    assert damaged == [("bridge_damaged", False, False)], seen
    assert any(POLICY in c["what"] for c in bot.mismatch_cards())


def test_open_with_a_stuck_writer_stays_closed_and_says_so_plainly(state, monkeypatch):
    """The route's own answer: a plain line and no PIN card, and the mismatch still stands."""
    from flask import Flask
    from puffpai_core.api.groups import bridge as route
    from puffpai_core.zone3.bridge import OPEN_BUSY_LINE
    monkeypatch.setattr(vault_anchor, "CHECK_WAIT_S", 0.2)
    z = Zone3(state, bus=Bus())
    z.vault.mismatch = True
    app = Flask("t")
    monkeypatch.setattr(route, "for_app", lambda _a: z)
    monkeypatch.setattr(route, "_names", lambda: {})
    held, end = threading.Event(), threading.Event()

    def wedged():
        with z.audit.write_lock:
            held.set()
            end.wait(30)
    t = in_a_thread(wedged)
    assert held.wait(5)
    try:
        with app.test_request_context("/v1/bridge/open", method="POST", json={}):
            body, status = route.bridge_open()
    finally:
        end.set()
        t.join(10)
    assert status == 503 and body.get_json() == {"error": OPEN_BUSY_LINE, "code": "bridge_busy"}
    assert z.vault.mismatch is True and z.bridge.posture()["state"] == "closed"


@pytest.mark.parametrize("name", [LEDGER, AUDIT])
def test_a_log_rolled_back_while_running_closes_with_its_own_writer_going(bot, name):
    """The Core running, the file put back under it. The writer's own check closes at its next line."""
    bot.write(name)
    old = bot.old_copy(name)
    bot.write(name)
    bot.write(name)
    bot.a.lock()
    for p, data in old.items():
        p.write_bytes(data)
    bot.write(name)
    bot.unlock()
    assert bot.state_now() == "closed"
    assert ("ledger_damaged" if name == LEDGER else "audit_damaged") in {c["kind"] for c in bot.z.cards}


@pytest.mark.parametrize("name", [POLICY, GENERATION])
def test_a_counter_rolled_back_that_grows_a_little_again_still_closes(bot, name):
    """Written while locked after the rollback: the expected value is the new one, but never lower than the vault's."""
    _rolled_back(bot, name)
    bot.write(name)
    bot.unlock()
    closes(bot, name)


@pytest.mark.xfail(strict=True, reason="NOT D151, here before it: a log rolled back, then one line written while the vault "
                                       "is locked, makes the new line the expected value, and logs have no number that "
                                       "only goes up across a rotation. Needs a check that the vault's own head is still "
                                       "in the file. Follow-up, said in the report.")
@pytest.mark.parametrize("name", [LEDGER, AUDIT])
def test_known_limit_a_log_rolled_back_that_grows_again_before_the_unlock(bot, name):
    _rolled_back(bot, name)
    bot.write(name)
    bot.unlock()
    closes(bot, name)


# -- pending values ---------------------------------------------------------------------------------------------------

def test_pending_values_are_taken_at_unlock(bot):
    bot.a.lock()
    for name in (LEDGER, AUDIT, POLICY, GENERATION):
        bot.write(name)
    assert set(bot.z.vault.pending) == set(ANCHORS)
    bot.unlock()
    stays_open(bot)
    assert bot.z.vault.pending == {}
    head, n = bot.z.audit.head()
    assert bot.a.anchor_get(AUDIT) == {"n": n, "h": head}
    assert bot.a.anchor_get(POLICY)["version"] == bot.z.bridge.policy.version
    assert bot.a.anchor_get(GENERATION)["generation"] == bot.z.consents.generation()


@pytest.mark.parametrize("name", [LEDGER, AUDIT, POLICY, GENERATION])
def test_files_older_than_the_pending_value_close(bot, name):
    """Locked, a pending value kept, then the files put back under the running Core, with no write after."""
    if name != GENERATION:
        bot.write(name)
    old = bot.old_copy(name)
    bot.a.lock()
    bot.write(name)
    assert name in bot.z.vault.pending
    for p, data in old.items():
        p.write_bytes(data)
    bot.unlock()
    closes(bot, name)


# -- a check that can't conclude --------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ANCHORS)
def test_a_check_that_cant_conclude_closes_and_says_so(bot, monkeypatch, name):
    """A writer that never finishes: never a silent pass. The owner hears "couldn't confirm", the Bridge closes."""
    monkeypatch.setattr(vault_anchor, "CHECK_WAIT_S", 0.2)
    lock = {LEDGER: bot.z.bridge.ledger.chain.write_lock, AUDIT: bot.z.audit.write_lock,
            POLICY: bot.z.bridge.policy.write_lock, GENERATION: bot.z.consents.write_lock}[name]
    bot.a.lock()
    held, end = threading.Event(), threading.Event()

    def wedged():
        with lock:
            held.set()
            end.wait(30)
    t = in_a_thread(wedged)
    assert held.wait(5)
    try:
        bot.unlock()
        assert bot.z.vault.problems == [{"what": name, "why": "cant_confirm"}]
    finally:
        end.set()
        t.join(10)
    closes(bot, name, CANT_CONFIRM_LINE)


def test_a_check_that_breaks_closes(bot, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("no")
    monkeypatch.setattr(vault_anchor, "_policy_value", broken)
    bot.a.lock()
    bot.unlock()
    assert bot.state_now() == "closed"
    assert [c for c in bot.z.cards if c["kind"] == "vault_mismatch"][0]["line"] == CANT_CONFIRM_LINE


# -- registered once --------------------------------------------------------------------------------------------------

def _count_checks(z):
    calls = []
    real = z.vault.unlocked

    def counted(*a, **k):
        calls.append(threading.current_thread().name)
        return real(*a, **k)
    z.vault.unlocked = counted
    return calls


def test_the_check_runs_once_per_real_unlock(bot):
    calls = _count_checks(bot.z)
    bot.a.lock()
    bot.unlock()
    assert len(calls) == 1, calls
    bot.a.lock()
    bot.unlock()
    assert len(calls) == 2, calls
    stays_open(bot)


def test_the_check_still_runs_when_filing_the_inbox_breaks(bot, monkeypatch):
    """Anyone can seal a box into the inbox, so a box that breaks the filing must not skip the check."""
    calls = _count_checks(bot.z)
    bot.a.lock()

    def broken(*_a, **_k):
        raise OSError("a box broke the filing")
    monkeypatch.setattr(bot.a, "_file_inbox", broken)
    bot.unlock()
    assert len(calls) == 1, calls


def test_with_no_archivist_of_its_own_an_unlock_it_hears_of_still_closes(state, monkeypatch):
    """Zone 3 could not reach its Archivist. It can't use on_unlock, so the Bus event is its only way to hear."""
    def fails(*_a, **_k):
        raise RuntimeError("no archivist")
    monkeypatch.setattr(vault_anchor, "find_archivist", fails)
    bus = Bus()
    z = Zone3(state, bus=bus)
    z.bridge.open()
    calls = _count_checks(z)
    assert not z.vault.wired and z.vault.problem == "archivist_failed"
    assert bus.publish(Bus.message("vault.unlocked", {"factor": "pin", "session": "s1"}, CORE))
    assert len(calls) == 1, calls
    assert z.bridge.posture()["state"] == "closed"


# -- the job's line: words only ---------------------------------------------------------------------------------------

WEB = {"package.json": '{"name": "garden-board", "scripts": {"start": "node server.js"}}', "server.js": "// a page\n",
       "README.md": "# Garden board\n\nA web page for your garden.\n"}
SAID = find.BRIDGE_CLOSED_LINE


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    yield w
    w.host.stop(timeout=10)


def _a_failed_build(world, tmp_path):
    repo = tmp_path / "repos" / "garden-board"
    for rel, text in WEB.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    world.ports.fixture_repo = repo
    cand = {"name": "garden-board", "full_name": "example/garden-board", "stars": 5, "description": "An app",
            "readme_steps": [], "runtime": "node", "tries_in": "native", "programs": [], "source": "github"}
    b = world.build_with_yes([cand], {"name": "A job", "does": "Helps", "sample_ask": "What now?"})
    world.ports.local_replies = Script([tool("download"), tool("give_up", reason="its server crashes on start"),
                                        tool("give_up", reason="its server crashes on start")])
    agent.Builder(world.link(b["id"])).run()
    return world.host.view(b["id"])["jobs"][0]


def test_an_ordinary_job_line_is_unchanged(world, tmp_path):
    job = _a_failed_build(world, tmp_path)
    assert job["status"] == "not_yet" and job["run"]["line"].startswith("No app worked yet. garden-board:")
    assert "Bridge" not in job["run"]["line"]


def test_a_crash_with_the_bridge_closed_keeps_todays_words(world, tmp_path):
    """Its files came, its server crashed. The Bridge reading closed is not why."""
    world.ports.bridge_closed = lambda: True
    line = _a_failed_build(world, tmp_path)["run"]["line"]
    assert line.startswith("No app worked yet. garden-board:") and "Bridge" not in line, line


def test_a_try_whose_download_was_refused_as_closed_says_so_and_how_to_open_it(world, tmp_path):
    world.ports.bridge_closed = lambda: True
    world.ports.allowed_hosts = set()
    job = _a_failed_build(world, tmp_path)
    line = job["run"]["line"]
    assert job["status"] == "not_yet" and line.startswith(SAID + " garden-board:"), line
    assert "No app worked yet" not in line and "door" not in line.lower()
    assert "If it is still closed" in SAID and "Bridge page" in SAID and "Check again" in SAID


def test_closed_for_one_host_with_the_bridge_open_keeps_todays_words(world, tmp_path):
    """The Core says "closed for this host" for a host off the plan too. The Bridge read open then, so it is not why."""
    world.ports.bridge_closed = lambda: False
    world.ports.allowed_hosts = set()
    assert _a_failed_build(world, tmp_path)["run"]["line"].startswith("No app worked yet.")


def test_the_line_stays_true_after_the_bridge_reopens(world, tmp_path, monkeypatch):
    """Refused as closed, then opened before the job ends: it was why, and "was" and "if it is still closed" stay true."""
    world.ports.allowed_hosts = set()
    closed = [True]
    world.ports.bridge_closed = lambda: closed[0]
    real = find.closed_now

    def then_opened(link):
        got = real(link)
        closed[0] = False
        return got
    monkeypatch.setattr(find, "closed_now", then_opened)
    assert _a_failed_build(world, tmp_path)["run"]["line"].startswith(SAID)
    assert closed == [False]


def test_a_broken_bridge_read_keeps_the_ordinary_words(world, tmp_path):
    def broken():
        raise RuntimeError("no")
    world.ports.bridge_closed = broken
    world.ports.allowed_hosts = set()
    assert _a_failed_build(world, tmp_path)["run"]["line"].startswith("No app worked yet.")


def test_one_app_refused_and_one_that_crashed_keeps_todays_words():
    both = [{"name": "a", "why": "x", "closed": True}, {"name": "b", "why": "it crashed", "closed": False}]
    assert find.not_yet_words(both) == "No app worked yet."
    assert find.not_yet_words([]) == "No app worked yet."
    assert find.not_yet_words(both[:1]) == SAID


def _cloud_world(tmp_path, state):
    """A cloud build on the real sender: the company's host refused the way the Core refuses it."""
    ports = FakePorts(tmp_path)
    w = World(tmp_path, state, ports)
    p = sender.load_providers()["anthropic"]
    w.host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(p), "cost_cap_usd": 5})
    ports.keys["toolsmith.anthropic"] = "sk-ant-" + "madeup" * 8                  # made up, and never sent: refused first
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "README.md").write_text("# A project\n", encoding="utf-8")
    ports.fixture_repo = repo
    return w


@pytest.mark.parametrize("closed,lead", [(True, SAID), (False, "No app worked yet.")])
def test_a_cloud_job_whose_every_call_to_the_company_was_refused(tmp_path, state, closed, lead):
    """The live shape, by the real path: Toolsmith's Builder, the Link, the Core's model route and sender, the wire's
    error, the look that gives way to the plan's apps, and the try that ends on the refusal. Only the ports are fake."""
    w = _cloud_world(tmp_path, state)
    try:
        w.ports.allowed_hosts = set()
        w.ports.bridge_closed = lambda: closed
        cand = {"name": "garden-board", "full_name": "example/garden-board", "stars": 5, "description": "An app",
                "readme_steps": ["npm start"], "runtime": "node", "tries_in": "native", "programs": [], "source": "github"}
        b = w.build_with_yes([cand], {"name": "A job", "does": "Helps", "sample_ask": "What now?"})
        agent.Builder(w.link(b["id"])).run()
        job = w.host.view(b["id"])["jobs"][0]
        line = job["run"]["line"]
        assert job["status"] == "not_yet" and line.startswith(lead), line
        assert "api.anthropic.com" in line and "nothing was sent" in line, line
        assert w.ports.sent == [], "nothing went out"
    finally:
        w.host.stop(timeout=10)


@pytest.mark.parametrize("seen,lead", [(True, SAID), (False, "No app worked yet.")])
def test_a_cloud_jobs_wind_up_uses_the_same_evidence(world, seen, lead):
    cand = {"name": "garden-board", "full_name": "example/garden-board", "source": "github"}
    b = world.build_with_yes([cand], {"name": "A job", "does": "Helps", "sample_ask": "What now?"})
    builder = agent.Builder(world.link(b["id"]))
    job = builder.b["jobs"][0]
    hooks = agent._Hooks(builder, job["id"])
    tried = [{"name": "garden-board", "why": "it did not start", "closed": seen}]
    search = scout.Search(builder, [job], {job["id"]: hooks}, {"try": None})
    search.tried[job["id"]] = tried
    search._wind_up("Toolsmith found nothing that fits")
    line = world.host.view(b["id"])["jobs"][0]["run"]["line"]
    assert line.startswith(lead + " Toolsmith found nothing that fits."), line


def test_the_real_bridge_says_when_it_reads_closed(state):
    z = Zone3(state, bus=Bus())
    ports = Ports(state, zone=z)
    assert ports.bridge_closed() is True                 # ships closed
    z.bridge.open()
    assert ports.bridge_closed() is False
    z.bridge.close()
    assert ports.bridge_closed() is True

    def broken():
        raise RuntimeError("no")
    assert Ports(state, zone=broken).bridge_closed() is False


def test_reading_the_state_opens_nothing(state):
    z = Zone3(state, bus=Bus())
    ports = Ports(state, zone=z)
    before = z.bridge.posture()
    for _ in range(3):
        ports.bridge_closed()
    assert z.bridge.posture() == before and before["state"] == "closed"


# -- the plan: said once, before her Yes, only when the Bridge really is why -------------------------------------------

JOB = {"name": "Dog training", "does": "Plans dog training", "sample_ask": ""}
JOB2 = {"name": "Bird counts", "does": "Counts birds in the garden", "sample_ask": ""}
PLAN_LINE = store.PLAN_BRIDGE_CLOSED_LINE


def _plan(world, tmp_path, *, topics=True, cache="cache"):
    """A real plan through Toolsmith's Finder and the Core's route. *topics*: it has a knowledge leg, so it gets a card."""
    world.ports.brain = lambda system, user: None
    world.ports.household_names = lambda: []
    bid = world.host.store.create("dog school and birds", [JOB, JOB2], world.host.settings())["id"]
    with pytest.MonkeyPatch.context() as m:
        m.setattr(agent.knowledge, "topics", lambda *_a, **_k: ["dog training"] if topics else [])
        agent.plan(world.link(bid), tmp_path / cache)
    return world.host.store.read(bid)


def _all_closed(world):
    world.ports.allowed_hosts = set()
    world.ports.bridge_closed = lambda: True


def test_a_plan_whose_every_search_the_closed_bridge_refused_says_so_once_first(world, tmp_path):
    _all_closed(world)
    b = _plan(world, tmp_path)
    lines = b["card"]["lines"]
    assert lines[0] == PLAN_LINE, lines
    text = " ".join(lines)
    assert text.count("The Bridge") == 1 and "Bridge is closed" not in text, lines       # two jobs, many refusals, one line
    assert "was closed when" in lines[0] and "If it is still closed" in lines[0]
    assert "Bridge page" in lines[0] and "door" not in text.lower()
    assert not [ln for ln in lines if ln.startswith(("Searched for this plan", "Not asked, since your search Yes"))], lines
    assert b["consent"]["id"], "no new block: the card still reaches her Yes"
    assert all(j["run"]["state"] == "waiting_yes" for j in b["jobs"])


def test_a_plan_with_no_card_says_so_on_its_status_line(world, tmp_path):
    _all_closed(world)
    b = _plan(world, tmp_path, topics=False)
    assert b["consent"]["said"] == f"{PLAN_LINE} {store.EMPTY_PLAN_LINE}"


def test_her_yes_binds_to_the_card_with_the_line_and_the_plan_sha_does_not_move(world, tmp_path, state):
    real = ConsentStore(state / "yes.db", Bus())
    world.ports.consent_request = lambda kind, target, covers, floor=False, bound=None: \
        real.ask(kind, target, covers, floor=floor, bound=bound)["consent_id"]
    _all_closed(world)
    b = _plan(world, tmp_path)
    rec = real.get(b["consent"]["id"])
    assert rec["covers"]["card"]["lines"][0] == PLAN_LINE
    others = json.loads(json.dumps(rec["covers"]))
    others["card"]["lines"].remove(PLAN_LINE)
    without = real.ask(rec["scope"]["kind"], rec["scope"]["target"], others, bound=rec["bound"])["card_sha"]
    assert without != rec["card_sha"], "the hash of what she reads must cover the line"
    with pytest.raises(ConsentError):                      # a Yes for the card without it is not a Yes for this one
        real.answer(rec["consent_id"], "yes", "tap", card_sha=without)
    assert real.get(rec["consent_id"])["status"] == "pending"
    assert real.answer(rec["consent_id"], "yes", "tap", card_sha=rec["card_sha"])["status"] == "granted"
    plain = store.card("g", [], [], {"where": "local"})
    closed = store.card("g", [], [], {"where": "local"}, search_closed=True)
    assert closed["lines"] == [PLAN_LINE] + plain["lines"] and closed["sentence"] == plain["sentence"]
    assert b["plan_sha"] == store.sha_of(b["envelope"]) and PLAN_LINE not in json.dumps(b["envelope"])


def test_some_searches_through_keeps_todays_words(world, tmp_path):
    """The search answers, the rest is refused, and the Bridge even reads closed by then: it is not why."""
    world.ports.allowed_hosts = {"api.github.com"}
    world.ports.bridge_closed = lambda: True
    world.ports.web = {"https://api.github.com/*": (200, json.dumps({"items": []}))}
    b = _plan(world, tmp_path)
    assert PLAN_LINE not in b["card"]["lines"]
    assert any(ln.startswith("Searched for this plan") for ln in b["card"]["lines"]), b["card"]["lines"]


def test_a_search_answered_from_the_cache_counts_as_through(world, tmp_path):
    world.ports.web = {"https://api.github.com/*": (200, json.dumps({"items": []}))}
    _plan(world, tmp_path, cache="kept")
    world.ports.allowed_hosts = set()
    world.ports.bridge_closed = lambda: True
    b = _plan(world, tmp_path, cache="kept")
    assert PLAN_LINE not in b["card"]["lines"], b["card"]["lines"]


@pytest.mark.parametrize("said", ["The Bridge needs your Yes for api.github.com first, so nothing was sent.",
                                  "That address could not be reached: the connection timed out."])
def test_refused_for_another_reason_keeps_todays_words(world, tmp_path, said):
    def refuses(*_a, **_k):
        raise Refused(said)
    world.ports.http_send_fn = refuses
    world.ports.bridge_closed = lambda: True
    b = _plan(world, tmp_path)
    assert PLAN_LINE not in b["card"]["lines"], b["card"]["lines"]


def test_refused_while_the_bridge_reads_open_keeps_todays_words(world, tmp_path):
    """A send refused for one host with the Bridge itself open (its switch off, say): the Bridge page's Open would not help."""
    world.ports.allowed_hosts = set()
    world.ports.bridge_closed = lambda: False
    b = _plan(world, tmp_path)
    assert PLAN_LINE not in b["card"]["lines"], b["card"]["lines"]
    assert "the Bridge is closed, so nothing was sent" in " ".join(b["card"]["lines"])


def test_a_plan_reopened_before_its_card_is_written_keeps_todays_words(world, tmp_path):
    """Refused, then open by the time the card is written: no line asks her to open what is open."""
    _all_closed(world)
    world.ports.bridge_closed = lambda: False
    b = _plan(world, tmp_path)
    assert PLAN_LINE not in b["card"]["lines"]


def test_a_card_read_after_the_bridge_reopened_still_reads_true(world, tmp_path):
    """The card's words can't change once it is raised, so the line must be true whichever way the Bridge is by then."""
    _all_closed(world)
    b = _plan(world, tmp_path)
    world.ports.bridge_closed = lambda: False
    assert world.host.store.read(b["id"])["card"]["lines"][0] == PLAN_LINE
    assert "was closed" in PLAN_LINE and "If it is still closed" in PLAN_LINE and "Until then" not in PLAN_LINE


def test_ports_that_cant_say_keep_todays_words(world, tmp_path):
    world.ports.allowed_hosts = set()
    b = _plan(world, tmp_path)
    assert PLAN_LINE not in b["card"]["lines"]
