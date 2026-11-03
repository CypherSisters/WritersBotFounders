"""D153: while a live build or an unfinished setup exists, the vault's idle timer is 60 minutes, not her setting.

The owner: "just the vault seems to be locked all the time during builds". Nothing touched the timer while a plan ran,
while the plan card waited on her, or during a build, so it locked 5 minutes after her last vault use. When the hold
ends her setting applies again, counted from her last real vault use, so it may lock at once. Only a real vault use
resets the timer (D68(e)). A real Archivist runs on a fake clock and a fake machine key, with the real host and the
real wiring's check: no keyring, no real vault, no network, no port of her bot."""

import json
import sys
import threading
import time

import pytest

from puffpai_core import jsonfile, termchat, wiring
from puffpai_core.api.app import wire_core
from puffpai_core.consent.store import ConsentStore
from puffpai_core.zone1 import archivist as archivist_mod
from puffpai_core.zone3.toolsmith_host import process, service, store as build_store
from toolsmith_fakes import FakePorts, World
from z1kit import PIN, FakeClock, make

MIN = 60
KEY = "sk-ant-" + "Z" * 40                     # made up: nothing could use it


class LiveChild:
    """Toolsmith's process, alive until the test ends it."""

    def __init__(self, on_exit) -> None:
        self.done = threading.Event()
        self.on_exit = on_exit
        self.stopped = False
        self.dead = False

    def wait(self, timeout=None) -> bool:
        return self.done.wait(timeout)

    def ended(self) -> bool:
        return self.dead or self.done.is_set()

    def stop(self) -> None:
        self.stopped = True
        self.finish(1)

    def finish(self, code: int = 0) -> None:
        if not self.done.is_set():
            self.on_exit(code)
            self.done.set()

    def die_with_nothing_said(self) -> None:
        """A crash the Core has not heard about yet: the process is gone, and its cleanup has not finished."""
        self.dead = True


class LiveRunner:
    def __init__(self) -> None:
        self.children = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        child = LiveChild(on_exit)
        self.children.append(child)
        return child


@pytest.fixture
def bot(tmp_path, state):
    """The real host and the real check, on a vault with a fake clock. Setup is finished unless a test says not."""
    clock = FakeClock()
    arch, _bus, events, _code = make(tmp_path / "vaultbot", clock=clock)
    w = World(tmp_path, state, ports=FakePorts(tmp_path))
    w.runner = LiveRunner()
    w.host.runner = w.runner
    w.host._now = clock
    _wizard(state, complete=True)
    arch.vault.hold = wiring.vault_hold(w.core, w.app, now=clock)

    class Bot:
        pass
    b = Bot()
    b.w, b.arch, b.clock, b.events, b.state = w, arch, clock, events, state
    return b


def _wizard(state, **fields) -> None:
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "wizard.json").write_text(json.dumps({"steps": {}, **fields}), encoding="utf-8")


def _draft(b) -> str:
    return b.w.host.draft("a home recipe bot", [{"name": "Recipes", "does": "Keeps recipes", "sample_ask": "Cook?"}])["id"]


def _reasons(b) -> list:
    return [m["payload"]["reason"] for m in b.events if m["type"] == "vault.locked"]


def _locks_at(b, minutes) -> None:
    """It locked with reason idle, by its own timer."""
    assert b.arch.vault.watch_once() is True
    assert not b.arch.is_open()
    assert _reasons(b) == ["idle"]
    assert b.clock() - 1000 == pytest.approx(minutes * MIN)


def _open_past_her_setting(b) -> None:
    b.clock.advance(6 * MIN)
    assert b.arch.vault.watch_once() is False
    assert b.arch.is_open()


# -- the plan stage: the longest stretch with no lease --------------------------------------------------------------

def test_a_plan_running_in_the_background_holds_it_to_60_minutes(bot):
    bid = _draft(bot)
    bot.w.host._launch("plan", bid)
    _open_past_her_setting(bot)
    bot.clock.advance(53 * MIN)
    assert bot.arch.vault.watch_once() is False           # 59 minutes
    bot.clock.advance(1 * MIN)
    _locks_at(bot, 60)


def test_a_plan_ending_with_no_card_goes_back_to_her_setting(bot):
    bid = _draft(bot)
    bot.w.host._launch("plan", bid)
    _open_past_her_setting(bot)
    bot.w.runner.children[0].finish(0)
    _locks_at(bot, 6)                                     # her 5 minutes are already past: it locks at once


def test_a_plan_waiting_on_her_card_holds_it_until_the_card_ends(bot):
    bid = _draft(bot)
    bot.w.host._card_out(bid)                             # what agent_plan does as it raises the plan card
    _open_past_her_setting(bot)
    bot.clock.advance(3 * MIN)
    assert bot.arch.vault.watch_once() is False           # 9 minutes: the card is still live
    bot.clock.advance(1 * MIN + 1)
    assert bot.arch.vault.watch_once() is True            # past its 10 minutes, even if nothing swept it
    assert _reasons(bot) == ["idle"]


def test_agent_plan_raising_its_card_counts_as_live(bot):
    bid = _draft(bot)
    bot.w.host.store.change(bid, lambda x: x.update(planning=True))
    jobs = [{"name": "Recipes", "does": "Keeps recipes", "sample_ask": "Cook?",
             "plan": {"candidates": [{"full_name": "example/app-a", "name": "app-a", "runtime": "python"}]}}]
    assert bot.w.host.holds_vault() is False
    bot.w.host.agent_plan(bid, {"jobs": jobs})
    assert bot.w.host.store.read(bid)["consent"]["id"]
    assert bot.w.host.holds_vault() is True


@pytest.mark.parametrize("status", ["declined", "expired"])
def test_her_no_or_no_answer_ends_the_cards_hold_at_once(bot, status):
    bid = _draft(bot)
    bot.w.host._card_out(bid)
    _open_past_her_setting(bot)
    bot.w.host._answered({"status": status, "consent_id": "c_1", "covers": {"build": bid}, "scope": {}})
    assert bot.arch.vault.watch_once() is True
    assert _reasons(bot) == ["idle"]


def test_her_yes_past_her_setting_does_not_lock_before_the_start_follows(bot):
    bid = _draft(bot)
    bot.w.host._card_out(bid)
    _open_past_her_setting(bot)
    bot.w.host._granted({"consent_id": "c_1", "covers": {"build": bid, "plan_sha": "x"}})
    assert bot.arch.vault.watch_once() is False           # the Yes is here, and the start is a moment behind it
    bot.w.host._launch("build", bid)
    assert bid not in bot.w.host._cards_out               # the process counts now
    bot.clock.advance(10 * MIN)
    assert bot.arch.vault.watch_once() is False


def test_a_yes_no_start_follows_ends_soon(bot):
    bid = _draft(bot)
    bot.w.host._card_out(bid)
    _open_past_her_setting(bot)
    bot.w.host._granted({"consent_id": "c_1", "covers": {"build": bid, "plan_sha": "x"}})
    bot.clock.advance(service.CARD_YES_S + 1)
    assert bot.arch.vault.watch_once() is True


# -- a build -----------------------------------------------------------------------------------------------------------

def test_a_live_build_stays_open_past_5_minutes_and_locks_at_60(bot):
    bot.w.host._launch("build", _draft(bot))
    _open_past_her_setting(bot)
    bot.clock.advance(53 * MIN)
    assert bot.arch.is_open()
    bot.clock.advance(1 * MIN)
    _locks_at(bot, 60)


def test_a_build_ending_goes_back_to_her_setting_counted_from_her_last_use(bot):
    bot.w.host._launch("build", _draft(bot))
    bot.clock.advance(2 * MIN)
    bot.w.runner.children[0].finish(0)
    assert bot.arch.vault.watch_once() is False           # 2 minutes in: her setting has 3 left
    assert bot.arch.vault.idle_left_s() == pytest.approx(3 * MIN)
    bot.clock.advance(3 * MIN)
    _locks_at(bot, 5)


def test_a_build_ending_past_her_setting_locks_at_once(bot):
    bot.w.host._launch("build", _draft(bot))
    bot.clock.advance(20 * MIN)
    assert bot.arch.vault.watch_once() is False
    bot.w.runner.children[0].finish(0)
    _locks_at(bot, 20)


def test_a_build_waiting_on_her_card_or_step_counts_by_its_process_and_only_that(bot):
    """A wait on her inside a build is Toolsmith's process waiting. A step can wait 7 days (``wait_step``), and the
    timer still stops at 60 minutes."""
    bid = _draft(bot)
    bot.w.host._launch("build", bid)
    bot.w.host.store.change(bid, lambda x: [j["run"].update(state="waiting_you") for j in x["jobs"]])
    _open_past_her_setting(bot)
    bot.clock.advance(54 * MIN)
    _locks_at(bot, 60)


# -- a dead build holds nothing ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("state_left", ["building", "waiting_you", "waiting_vault", "planned", "waiting_yes"])
def test_a_crashed_build_with_a_stale_stored_status_holds_nothing(bot, tmp_path, state, state_left):
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    bid = _draft(bot)
    bot.w.host.store.change(bid, lambda x: [j["run"].update(state=state_left) for j in x["jobs"]])
    # The Core restarted: a new host, on the same files, with no process.
    host = ToolsmithHost(state, FakePorts(tmp_path), bot.w.registry, bot.w.core.tokens, runner=LiveRunner())
    bot.w.app.extensions["puffpai_toolsmith"] = host
    assert host.holds_vault() is False
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


def test_a_process_that_died_with_nothing_said_holds_nothing(bot):
    bot.w.host._launch("build", _draft(bot))
    assert bot.w.host.holds_vault() is True
    bot.w.runner.children[0].die_with_nothing_said()
    assert bot.w.host.holds_vault() is False


def test_a_paused_build_holds_nothing(bot):
    bot.w.host._launch("build", _draft(bot))
    assert bot.w.host.holds_vault() is True
    bot.w.host.pause("A check failed.")
    assert bot.w.host.holds_vault() is False
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


def test_a_stopped_core_holds_nothing(bot):
    bot.w.host._card_out(_draft(bot))
    bot.w.host.stop(timeout=1, grace_s=0)
    assert bot.w.host.holds_vault() is False


def test_safe_mode_holds_nothing_not_even_setup(bot, state):
    _wizard(state, complete=False)
    bot.w.host._launch("build", _draft(bot))
    bot.w.core.caretaker._runtime_reasons.append("A check failed.")
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


def test_the_vaults_own_safe_mode_is_never_held(bot):
    bot.w.host._launch("build", _draft(bot))
    bot.arch.vault._safe = "no_recovery"
    assert bot.arch.vault._idle_limit() == 5 * MIN


# -- setup -------------------------------------------------------------------------------------------------------------

def test_setup_not_finished_holds_it_to_60_minutes(bot, state):
    _wizard(state, complete=False)
    _open_past_her_setting(bot)
    bot.clock.advance(54 * MIN)
    _locks_at(bot, 60)


def test_setup_finishing_goes_back_to_her_setting(bot, state):
    _wizard(state, complete=False)
    _open_past_her_setting(bot)
    _wizard(state, complete=True)
    bot.clock.advance(wiring.SETUP_READ_S)                # the file is read at most this often
    assert bot.arch.vault.watch_once() is True
    assert _reasons(bot) == ["idle"]


@pytest.mark.parametrize("words", [None, "not json", "[]", "{}", '{"complete": "no"}'])
def test_setup_it_cannot_read_holds_nothing(bot, state, words):
    path = state / "settings" / "wizard.json"
    if words is None:
        path.unlink()
    else:
        path.write_text(words, encoding="utf-8")
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


def test_a_finished_bot_running_a_step_again_is_not_held(bot, state):
    _wizard(state, complete=True, partial={"brain": {"answers": {}, "settled": []}})
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


def test_a_finished_bot_with_no_build_locks_as_today(bot):
    bot.clock.advance(5 * MIN - 1)
    assert bot.arch.is_open()
    bot.clock.advance(1)
    assert not bot.arch.is_open()                         # a status read locks it too, as today
    assert _reasons(bot) == ["idle"]


# -- a check that can't tell holds nothing -----------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [False, None, 1, "yes", [1]])
def test_anything_but_a_plain_true_holds_nothing(state, answer):
    clock = FakeClock()
    a, _, _, _ = make(state, clock=clock)
    a.vault.hold = lambda: answer
    clock.advance(5 * MIN)
    assert a.vault.watch_once() is True


def test_a_check_that_raises_holds_nothing(state):
    clock = FakeClock()
    a, _, events, _ = make(state, clock=clock)

    def boom():
        raise RuntimeError("can't tell")
    a.vault.hold = boom
    clock.advance(5 * MIN)
    assert a.vault.watch_once() is True
    assert [m["payload"]["reason"] for m in events if m["type"] == "vault.locked"] == ["idle"]


def test_a_host_that_raises_holds_nothing(bot, monkeypatch):
    bot.w.host._launch("build", _draft(bot))
    monkeypatch.setattr(bot.w.host, "holds_vault", lambda: 1 / 0)
    bot.clock.advance(5 * MIN)
    _locks_at(bot, 5)


# -- what stays immediate, and what stays the same --------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["owner", "safe_mode"])
def test_a_hand_lock_and_safe_mode_lock_at_once_during_a_build_and_end_leases(bot, reason):
    bot.arch.put_secret("toolsmith.anthropic", KEY)
    h = bot.arch.lend("toolsmith.anthropic", "toolsmith", "build:x", hosts=("api.anthropic.com",), job="build:x")
    bot.w.host._launch("build", _draft(bot))
    bot.clock.advance(1)
    bot.arch.lock(reason)
    assert not bot.arch.is_open()
    last = json.loads(bot.arch.vault.log_path.read_text(encoding="utf-8").splitlines()[-2])
    assert (last["action"], last["reason"]) == ("lock", reason)
    assert not bot.arch.live(h)


def test_leases_still_end_at_3_hours_and_outlive_the_idle_lock(bot):
    bot.arch.put_secret("toolsmith.anthropic", KEY)
    h = bot.arch.lend("toolsmith.anthropic", "toolsmith", "build:x", hosts=("api.anthropic.com",), job="build:x")
    bot.w.host._launch("build", _draft(bot))
    bot.clock.advance(60 * MIN)
    assert bot.arch.vault.watch_once() is True            # the idle lock fired during a live build
    got = bot.arch.use(h, borrower="toolsmith", purpose="build:x", host="api.anthropic.com")
    assert got == KEY.encode()                            # the lease does not end with it (X22)
    bot.clock.advance(120 * MIN)
    assert not bot.arch.live(h)                           # 3 hours, hold or no hold (D41)


def test_a_lease_being_used_is_not_a_vault_use(bot):
    """D68(e) stays: a lease use does not move the timer. So the hold is what keeps it open, not the lease."""
    bot.arch.put_secret("toolsmith.anthropic", KEY)
    h = bot.arch.lend("toolsmith.anthropic", "toolsmith", "build:x", hosts=("api.anthropic.com",), job="build:x")
    touched = bot.arch.vault._last_touch
    bot.clock.advance(MIN)
    bot.arch.use(h, borrower="toolsmith", purpose="build:x", host="api.anthropic.com")
    assert bot.arch.vault._last_touch == touched


def test_a_real_vault_use_still_resets_the_timer_and_a_held_one_does_not(bot):
    bot.w.host._launch("build", _draft(bot))
    bot.clock.advance(30 * MIN)
    touched = bot.arch.vault._last_touch
    assert bot.arch.is_open() and bot.arch.vault.watch_once() is False
    assert bot.arch.vault.status()["state"] == "open"
    assert bot.arch.vault._last_touch == touched          # asking touches nothing
    bot.arch.put_secret("some.key", KEY)                  # a real use
    assert bot.arch.vault._last_touch == bot.clock()
    bot.clock.advance(59 * MIN)
    assert bot.arch.vault.watch_once() is False
    bot.clock.advance(1 * MIN)
    assert bot.arch.vault.watch_once() is True


# -- the log and status stay true --------------------------------------------------------------------------------------

def test_status_says_the_time_in_force_and_her_setting_is_never_rewritten(bot):
    v = bot.arch.vault
    v.set_idle_minutes(10)
    saved = v.settings_path.read_bytes()
    assert (v.status()["idle_lock_minutes"], v.status()["idle_own_minutes"]) == (10, 10)
    bot.w.host._launch("build", _draft(bot))
    st = v.status()
    assert (st["idle_lock_minutes"], st["idle_own_minutes"]) == (60, 10)
    assert st["idle_left_s"] == 60 * MIN
    bot.clock.advance(7 * MIN)
    assert v.status()["idle_left_s"] == 53 * MIN
    assert v.idle_s == 10 * MIN                           # her setting
    assert v.settings_path.read_bytes() == saved
    bot.w.runner.children[0].finish(0)
    st = v.status()
    assert (st["idle_lock_minutes"], st["idle_own_minutes"]) == (10, 10)
    assert st["idle_left_s"] == 3 * MIN


def test_the_hold_writes_nothing_to_the_vault_log_and_an_unlock_stays_one_line(bot):
    bot.w.host._launch("build", _draft(bot))
    log = bot.arch.vault.log_path
    before = log.read_text(encoding="utf-8")
    for _ in range(40):
        bot.clock.advance(30)
        assert bot.arch.vault.watch_once() is False
        bot.arch.vault.status()
    assert log.read_text(encoding="utf-8") == before
    bot.arch.lock()
    bot.arch.unlock("pin", PIN)
    added = [json.loads(x)["action"] for x in log.read_text(encoding="utf-8").splitlines()[len(before.splitlines()):]]
    assert added == ["lock", "unlock"]


# -- the wiring --------------------------------------------------------------------------------------------------------

def test_the_core_wires_the_hold_into_its_own_vault_and_the_vault_imports_no_toolsmith(tmp_path, state):
    w = World(tmp_path, state, ports=FakePorts(tmp_path))
    wire_core(w.app)                                      # what start_core runs first
    v = archivist_mod.for_state(w.state).vault
    assert callable(v.hold)
    _wizard(state, complete=False)
    assert v.hold() is True
    from puffpai_core.zone1 import vault as vault_mod
    assert "toolsmith" not in vault_mod.__dict__
    source = open(vault_mod.__file__, encoding="utf-8").read().lower()
    assert "import toolsmith" not in source and "zone3" not in source


def test_the_check_takes_no_lock_the_seal_writers_hold(bot):
    """It runs with the vault's lock held, and those writers take theirs first: waiting on one would be a deadlock."""
    bot.w.host._launch("build", _draft(bot))
    got = {}

    def ask():
        got["held"] = bot.w.host.holds_vault()
    with bot.w.host._lock:
        th = threading.Thread(target=ask)
        th.start()
        th.join(5)
    assert got.get("held") is True


def test_a_seal_does_not_change_what_status_says_and_publishes_nothing_she_sees(bot):
    """The busy ``stored`` and ``anchor_put`` log lines are Core seals. No screen hears them: no Bus event comes."""
    before = len(bot.events)
    for n in range(5):
        assert bot.arch.anchor_put("core.audit", {"n": n, "h": "x"}) is True
        assert bot.arch.vault.status()["state"] == "open"
    assert len(bot.events) == before


# -- Core seals are not a use of the vault ------------------------------------------------------------------------------

def test_a_finished_bot_with_a_busy_audit_log_locks_at_her_setting(bot):
    for n in range(4):
        bot.clock.advance(MIN)
        assert bot.arch.anchor_put("core.audit", {"n": n, "h": "x"}) is True
        assert bot.arch.anchor_get("core.audit") == {"n": n, "h": "x"}
    bot.clock.advance(MIN)
    assert bot.arch.vault.watch_once() is True            # 5 minutes after her last real use, seals or no seals
    assert _reasons(bot) == ["idle"]


def test_a_seal_with_a_hold_does_not_stretch_the_60_minutes(bot):
    """One line an hour must not keep it open: the case that stayed open for 295 minutes."""
    bot.w.host._launch("build", _draft(bot))
    for n in range(59):
        bot.clock.advance(MIN)
        assert bot.arch.anchor_put("bridge.ledger", {"n": n, "h": "x"}) is True
    bot.clock.advance(MIN)
    _locks_at(bot, 60)


def test_a_seal_still_needs_an_open_vault(bot):
    assert bot.arch.anchor_put("core.audit", {"n": 1, "h": "x"}) is True
    bot.clock.advance(5 * MIN)
    assert bot.arch.anchor_put("core.audit", {"n": 2, "h": "x"}) is False       # it goes pending: nothing kept here
    assert not bot.arch.is_open()
    with pytest.raises(archivist_mod.Locked):
        bot.arch.anchor_get("core.audit")
    bot.arch.unlock("pin", PIN)
    assert bot.arch.anchor_get("core.audit") == {"n": 1, "h": "x"}


def test_an_unlock_check_reading_anchors_leaves_the_timer_where_the_unlock_set_it(bot):
    seen = []
    bot.arch.anchor_put("core.audit", {"n": 1, "h": "x"})
    bot.arch.lock()
    bot.clock.advance(1)
    bot.arch.on_unlock(lambda a: seen.append((a.anchor_get("core.audit"), a.vault.is_open())))
    bot.arch.unlock("pin", PIN)
    assert seen == [({"n": 1, "h": "x"}, True)]
    assert bot.arch.vault._last_touch == bot.clock()


@pytest.mark.parametrize("use", ["meta", "items", "get", "put", "put_secret", "lend"])
def test_real_uses_still_reset_the_timer(bot, use):
    bot.arch.put_secret("some.key", KEY)
    touched = bot.arch.vault._last_touch
    bot.clock.advance(MIN)
    {"meta": lambda: bot.arch.vault.meta("some.key"), "items": lambda: bot.arch.vault.items(),
     "get": lambda: bot.arch.vault.get("some.key"), "put": lambda: bot.arch.vault.put("other", b"x"),
     "put_secret": lambda: bot.arch.put_secret("other.key", KEY),
     "lend": lambda: bot.arch.lend("some.key", "toolsmith", "build:x", hosts=("a.example",), job="build:x")}[use]()
    assert bot.arch.vault._last_touch > touched


def test_a_lock_in_the_middle_of_a_status_read_does_not_fail_it(bot):
    """It raised ``TypeError``, so the route failed, and a page reads a failed read as not open."""
    v = bot.arch.vault
    bot.arch.put_secret("some.key", KEY)
    real = v.ways_in
    v.ways_in = lambda: (v.lock("idle"), real())[1]
    st = v.status()
    assert st["state"] == "open" and st["items"] == 1     # what was true when the read began
    assert v.status()["state"] == "locked"


def test_seals_and_unlocks_at_the_same_time_stay_sound(bot):
    errors, seals = [], [0]
    stop = threading.Event()

    def sealer():
        n = 0
        while not stop.is_set():
            n += 1
            try:
                if bot.arch.anchor_put("core.audit", {"n": n, "h": "x"}):
                    seals[0] += 1
                bot.arch.vault.status()
            except Exception as exc:  # noqa: BLE001 - what this test looks for
                errors.append(type(exc).__name__)
    th = threading.Thread(target=sealer)
    th.start()
    try:
        for _ in range(3):
            bot.arch.lock("idle")
            bot.arch.unlock("pin", PIN)
    finally:
        stop.set()
        th.join(20)
    assert not th.is_alive() and errors == [] and seals[0] > 0
    assert bot.arch.anchor_get("core.audit")["n"] >= 1


# -- a real process ------------------------------------------------------------------------------------------------------

@pytest.fixture
def real_process(bot, monkeypatch, tmp_path):
    """The real runner, the real ``Child`` and a real sleeping process, ended by the test that started it."""
    spawn = process.spawn
    monkeypatch.setattr(process, "spawn", lambda argv, cwd, env, log, stdin=None: spawn(
        [sys.executable, "-I", "-c", "import time; time.sleep(90)"], cwd, env, log, stdin=stdin))
    bot.w.host.runner = process.ProcessRunner("http://127.0.0.1:9", tmp_path / "plog")
    return bot.w.host


def _gone(child) -> None:
    child.job.proc.kill()
    child.job.proc.wait(10)
    end = time.monotonic() + 5
    while child.job.proc.returncode is None and time.monotonic() < end:
        time.sleep(0.01)


def test_a_real_process_holds_while_it_runs_and_not_once_killed(bot, real_process):
    child = real_process._launch("build", _draft(bot))
    assert isinstance(child, process.Child) and child.ended() is False
    assert real_process.holds_vault() is True
    _gone(child)
    assert child.ended() is True
    assert real_process.holds_vault() is False
    assert child.wait(15)


def test_a_killed_process_holds_nothing_while_its_cleanup_is_stuck(bot, real_process):
    bid = _draft(bot)
    stuck = real_process.knowledge._lock(bid)
    stuck.acquire()
    try:
        child = real_process._launch("build", bid)
        _gone(child)
        assert child.wait(1) is False                     # its cleanup is blocked: ``done`` has not come
        assert real_process.holds_vault() is False
        bot.clock.advance(5 * MIN)
        assert bot.arch.vault.watch_once() is True
    finally:
        stuck.release()
    assert child.wait(15)


# -- more of what counts as live --------------------------------------------------------------------------------------

def test_a_store_phase_thread_counts_while_it_lives(bot):
    host = bot.w.host
    assert host.store_phase.alive() is False
    done = threading.Event()
    th = threading.Thread(target=done.wait, daemon=True)
    host.store_phase._threads["b"] = th
    th.start()
    assert host.store_phase.alive() is True and host.holds_vault() is True
    done.set()
    th.join(5)
    assert host.store_phase.alive() is False and host.holds_vault() is False


def test_a_check_again_card_counts_as_live(tmp_path, state):
    from test_i18e_check_again import _started, _world
    w = _world(tmp_path, state)
    b, _cid = _started(w)
    assert w.host.holds_vault() is False
    held = [j["id"] for j in b["jobs"]][:1]
    w.host._again_ask(w.host.store.read(b["id"]), held, w.host.settings(), "Check again.")
    assert w.host.holds_vault() is True


@pytest.mark.parametrize("answer", ["no", "expire"])
def test_a_real_consent_store_ending_the_card_ends_its_hold(bot, state, answer):
    host, consents = bot.w.host, ConsentStore(state / "yes.db")
    consents.on_answer(host._answered)
    bid = _draft(bot)
    sha = "a" * 64
    card = consents.ask("package", build_store.bridge_target(bid), {"build": bid, "plan_sha": sha}, bound={"plan_sha": sha})
    host._card_out(bid)
    assert host.holds_vault() is True
    if answer == "no":
        consents.answer(card["consent_id"], "no")
    else:
        consents.expire(card["consent_id"])
    assert host.holds_vault() is False


def test_a_paused_host_holds_nothing_even_with_setup_unfinished(bot, state):
    _wizard(state, complete=False)
    assert bot.arch.vault.hold() is True
    bot.w.host.pause("A check failed.")
    assert bot.arch.vault.hold() is False


def test_a_stopping_host_holds_nothing_even_with_setup_unfinished(bot, state):
    _wizard(state, complete=False)
    bot.w.host.stop(timeout=1, grace_s=0)
    assert bot.arch.vault.hold() is False


# -- reading wizard.json as little as can be ----------------------------------------------------------------------------

@pytest.fixture
def reads(monkeypatch):
    real, got = jsonfile.read, []

    def read(path, default):
        if str(path).endswith("wizard.json"):
            got.append(path)
            if getattr(read, "boom", False):
                raise PermissionError("in use")
        return real(path, default)
    monkeypatch.setattr(jsonfile, "read", read)
    return read, got


def test_a_live_build_answers_with_no_read_of_the_file(bot, reads):
    bot.w.host._launch("build", _draft(bot))
    assert bot.arch.vault.hold() is True
    assert reads[1] == []


def test_the_file_is_read_at_most_once_in_a_while(bot, state, reads):
    _wizard(state, complete=False)
    hold = bot.arch.vault.hold
    assert [hold() for _ in range(20)] == [True] * 20
    assert len(reads[1]) == 1
    bot.clock.advance(wiring.SETUP_READ_S)
    assert hold() is True
    assert len(reads[1]) == 2


def test_complete_is_remembered_for_this_core_run(bot, state, reads):
    _wizard(state, complete=True)
    hold = bot.arch.vault.hold
    assert hold() is False
    bot.clock.advance(wiring.SETUP_READ_S * 10)
    assert hold() is False
    assert len(reads[1]) == 1


def test_a_read_that_fails_keeps_the_last_answer(bot, state, reads):
    _wizard(state, complete=False)
    hold = bot.arch.vault.hold
    assert hold() is True
    reads[0].boom = True
    bot.clock.advance(wiring.SETUP_READ_S)
    assert hold() is True                                 # a sharing clash does not drop the hold
    reads[0].boom = False
    _wizard(state, complete=True)
    bot.clock.advance(wiring.SETUP_READ_S)
    assert hold() is False


def test_a_first_read_that_fails_holds_nothing(bot, reads):
    reads[0].boom = True
    assert bot.arch.vault.hold() is False


# -- the words -----------------------------------------------------------------------------------------------------------

class _Say:
    """The chat's /vault and /unlock on a made-up status: only the words they say."""

    def __init__(self, status: dict) -> None:
        self.chat = termchat.Chat.__new__(termchat.Chat)
        self.lines: list = []
        self.chat.say = lambda text, *a, **k: self.lines.append(text)
        self.chat.look = type("L", (), {"paint": staticmethod(lambda text, *a, **k: text)})()
        self.chat.api = type("A", (), {"call": staticmethod(lambda method, path, body=None: (200, status))})()


def test_the_chat_says_after_that_while_held_and_not_otherwise():
    held = _Say({"state": "open", "idle_lock_minutes": 60, "idle_own_minutes": 5})
    held.chat._unlock(PIN)
    assert "It locks again after 60 minutes with no use, or with /lock. That is while a build or setup is going. " \
           "After that, 5 minutes." in held.lines[-1]
    plain = _Say({"state": "open", "idle_lock_minutes": 5, "idle_own_minutes": 5})
    plain.chat._unlock(PIN)
    assert "That is while" not in plain.lines[-1]
    assert termchat.idle_after({"idle_lock_minutes": 60, "idle_own_minutes": 5}).endswith("After that, 5 minutes.")
    assert termchat.idle_after({"idle_lock_minutes": 60}) == ""


def test_the_lent_line_says_only_what_is_true():
    assert termchat.VAULT_LENT == "What already has its key goes on."
    logic = (service.Path(__file__).parent.parent / "packages" / "desktop" / "web" / "logic.js").read_text(encoding="utf-8")
    assert 'const VAULT_LENT = "What already has its key goes on.";' in logic
    assert "until you unlock it" not in logic.split("VAULT_LENT")[1]


def test_the_windows_row_compares_what_it_says():
    web = service.Path(__file__).parent.parent / "packages" / "desktop" / "web" / "window.js"
    text = web.read_text(encoding="utf-8")
    assert "const tail = L.vaultRowWords(waits, S.vault);" in text and "if (tail === shownFor) return;" in text
    assert "if (waits === shownFor) return;" not in text


def test_the_contract_names_both_idle_fields():
    contract = (service.Path(__file__).parent.parent / "contracts" / "openapi.yaml").read_text(encoding="utf-8")
    assert "idle_lock_minutes is the idle time in force now" in contract and "idle_own_minutes is her setting" in contract
