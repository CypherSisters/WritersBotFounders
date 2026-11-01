"""Lane I19a (D68): once the owner says Yes to a build, Toolsmith keeps the keys it needs until the build ends.

Her report (test4, 10-28): she kept having to unlock the vault for Toolsmith. The lease already outlived the idle
lock (``Archivist._on_vault`` ends leases only on a hand lock, tamper or a provider change), but it was taken
lazily at the build's first cloud call, and the vault idle-locks 5 minutes after the last vault use. Reading a
plan card or chatting for 5 minutes was enough: her Yes landed on a locked vault and the build waited for a trip
to the vault page. Her GitHub key was lent per search, so it went unkeyed after any idle lock.

Now her Yes borrows the build's keys at once (the cloud key, and the GitHub key when the vault is open), kept
until the build's process ends, a hand lock, or 3 hours (X22, unchanged). When the vault is locked at her Yes
the build view says ``keys.unlock`` and the Desktop asks for her PIN on the same card. The real Archivist runs
here on a fake clock and a fake machine key: no keyring, no real vault, no network, no port of her bot."""

import json
import subprocess

import pytest

from conftest import REPO
from puffpai_core.zone1 import archivist as archivist_mod
from puffpai_core.zone3.toolsmith.errors import CloudWaitVault, Refused
from puffpai_core.zone3.toolsmith_host import sender, service, store
from puffpai_core.zone3.toolsmith_host.ports import Ports
from test_desktop_js import NODE
from toolsmith_fakes import FakePorts, World, _Done
from z1kit import PIN, FakeClock, make

KEY = "sk-ant-" + "Z" * 40                     # made up: only the fake company could see it
GH_KEY = "github_pat_" + "0Fake" * 8          # made up, a GitHub key's shape
WEB = REPO / "packages" / "desktop" / "web"

#: Unrelated goals (BuildBot is a factory): a home recipe bot, a small-business invoices bot, a birdwatching bot.
GOALS = [
    ("a home recipe bot", [{"name": "Recipes", "does": "Keeps family recipes", "sample_ask": "What can I cook?"}]),
    ("a small-business invoices bot", [{"name": "Invoices", "does": "Makes and tracks invoices",
                                        "sample_ask": "Who owes me?"},
                                       {"name": "Clients", "does": "Keeps client details", "sample_ask": "Add a client"}]),
    ("a birdwatching bot", [{"name": "Sightings", "does": "Logs birds seen", "sample_ask": "What did I see today?"}]),
]
CANDS = [{"full_name": "example/app-a", "name": "app-a", "runtime": "python"}]


class Recorder:
    """Toolsmith's process, recorded: it "runs" until the test ends it (``host._child_done``)."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str]] = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.runs.append((do, build_id))
        return _Done(0)


class VaultPorts(FakePorts):
    """FakePorts, but the key ports are the production ones on a real Archivist (fake clock, fake machine)."""

    def __init__(self, tmp, arch) -> None:
        super().__init__(tmp)
        self.arch = arch

    def _archivist(self):
        return self.arch

    lend_key = Ports.lend_key
    key_value = Ports.key_value
    end_lease = Ports.end_lease
    vault_open = Ports.vault_open
    lease_live = Ports.lease_live


def _world(tmp_path, state, *, cloud=True, github=True):
    clock = FakeClock()
    arch, _bus, _events, _code = make(tmp_path / "vaultbot", clock=clock)
    arch.put_secret("toolsmith.anthropic", KEY)
    if github:
        arch.put_secret(service.GITHUB_KEY_NAME, GH_KEY)
    w = World(tmp_path, state, ports=VaultPorts(tmp_path, arch))
    w.runner = Recorder()
    w.host.runner = w.runner
    (state / "settings").mkdir(parents=True, exist_ok=True)
    if cloud:
        prov = w.host.providers()["anthropic"]
        (state / "settings" / "toolsmith.json").write_text(json.dumps({
            "where": "cloud", "provider": "anthropic", "model": "claude-opus-5-5", "cost_cap_usd": 5.0,
            "consent": {"sha": sender.consent_sha(prov), "at": "2026-10-28T03:00:00+00:00", "by": "owner"}}),
            encoding="utf-8")
    return w, arch, clock


def _planned(w, goal, jobs):
    """A build with its plan and its plan card raised (no Yes yet)."""
    st = w.host.settings()
    b = w.host.store.create(goal, jobs, st)
    w.host.store.change(b["id"], lambda x: x.update(model={"where": st["where"], "provider": st.get("provider"),
                                                           "model": st.get("model")}))
    b = w.host.store.set_plan(b["id"], {"jobs": [{**j, "plan": {"candidates": CANDS}} for j in jobs]}, st,
                              "Anthropic" if st["where"] == "cloud" else "")
    covers = {"build": b["id"], "plan_sha": b["plan_sha"], "card": b["card"],
              "install_hosts": b["envelope"]["install_hosts"]}
    if st["where"] == "cloud":                         # as a real cloud plan card holds them (D121)
        covers.update(reads_public=True, tries_found=True)
    cid = w.ports.consent_request("package", store.bridge_target(b["id"]), covers, bound={"plan_sha": b["plan_sha"]})
    w.host.store.change(b["id"], lambda x: x["consent"].update(id=cid))
    return b["id"], cid


def _yes(w, bid, cid):
    """Her Yes on the plan card (the consent store's Yes listener runs), then the start every path asks for."""
    w.ports.consent.say_yes(cid)
    return w.host.start(bid, cid)


def _adding(w, monkeypatch):
    """Ticked store apps (or a model) are added before the build (lane I16): the store phase claims the build at
    her Yes. Only the claim is faked; the build itself starts through the real ``start_now``."""
    def begin(build_id, consent_id):
        w.host.store.change(build_id, lambda x: x.update(store_adds={"consent_id": consent_id, "state": "adding",
                                                                     "apps": [], "line": "", "said": []}))
        return True
    monkeypatch.setattr(w.host.store_phase, "begin", begin)


def _added(w, bid, cid):
    """The store phase is done: it starts the build, as ``StorePhase._finish`` does."""
    w.host.store.change(bid, lambda x: x["store_adds"].update(state="starting"))
    w.host.start_now(bid, cid)


def _mine(arch, bid):
    return [x for x in arch.leases() if x["holder"] == "toolsmith" and x["key"] == "toolsmith.anthropic"
            and x["purpose"] == f"build:{bid}"]


def _gh(arch):
    return [x for x in arch.leases() if x["key"] == service.GITHUB_KEY_NAME]


def _idle(arch, clock):
    clock.advance(arch.vault.idle_s + 1)
    assert arch.is_open() is False                     # the idle lock really fired
    assert any(json.loads(ln).get("reason") == "idle"
               for ln in arch.vault.log_path.read_text(encoding="utf-8").splitlines())


def _unlocks(arch) -> int:
    return sum(1 for ln in arch.vault.log_path.read_text(encoding="utf-8").splitlines()
               if json.loads(ln).get("action") == "unlock")


# -- an idle lock during a build never stalls it ------------------------------------------------

@pytest.mark.parametrize("goal,jobs", GOALS, ids=["recipes", "invoices", "birds"])
def test_yes_lends_keys_for_the_whole_build_and_the_idle_lock_does_not_end_them(tmp_path, state, goal, jobs):
    w, arch, clock = _world(tmp_path, state)
    bid, cid = _planned(w, goal, jobs)
    w.ports.consent.say_yes(cid)
    assert arch.leases() == []                      # nothing to add first: the start right after her Yes lends
    w.host.start(bid, cid)
    assert w.runner.runs == [("build", bid)]
    assert len(_mine(arch, bid)) == 1 and len(_gh(arch)) == 1      # before any cloud call
    w.host.start(bid, cid)                                          # a second start (another page) lends no more
    assert len(_mine(arch, bid)) == 1 and len(_gh(arch)) == 1
    _idle(arch, clock)                                              # she reads, chats: 5 minutes go by
    clock.advance(40 * 60)
    assert w.host.agent_model(bid, {"probe": True}) == {"ok": True}  # the agent's vault-wait poll: fine
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY     # the next cloud call has its key
    extra, said = w.host._github_key(bid)
    assert said == "sent" and extra["Authorization"].endswith(GH_KEY[-8:])
    extra.clear()
    assert w.host.view(bid)["keys"] == {"cloud": True, "leased": True, "unlock": False}
    assert _unlocks(arch) == 0                                      # she never had to unlock
    w.host._child_done(bid, "build", 0)                             # the build ends: its keys go back
    assert arch.leases() == []


def test_the_github_key_is_kept_for_a_build_on_this_computers_model_too(tmp_path, state):
    w, arch, clock = _world(tmp_path, state, cloud=False)
    bid, cid = _planned(w, *GOALS[2])
    _yes(w, bid, cid)
    assert len(_gh(arch)) == 1 and _mine(arch, bid) == []
    _idle(arch, clock)
    extra, said = w.host._github_key(bid)
    assert said == "sent"
    extra.clear()
    assert w.host.view(bid)["keys"]["unlock"] is False             # a local build never asks for her PIN
    w.host._child_done(bid, "build", 0)
    assert arch.leases() == []


def test_a_plan_process_ending_never_ends_the_keys_of_her_yes(tmp_path, state):
    w, arch, _clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[0])
    _yes(w, bid, cid)
    w.host._child_done(bid, "plan", 0)                              # the planner exits just after her Yes
    assert len(_mine(arch, bid)) == 1 and len(_gh(arch)) == 1


# -- a hand lock ends them -----------------------------------------------------------------

@pytest.mark.parametrize("goal,jobs", GOALS[:2], ids=["recipes", "invoices"])
def test_a_hand_lock_ends_every_key_of_the_build(tmp_path, state, goal, jobs):
    w, arch, _clock = _world(tmp_path, state)
    bid, cid = _planned(w, goal, jobs)
    _yes(w, bid, cid)
    arch.lock("owner")
    assert arch.leases() == []
    with pytest.raises(CloudWaitVault) as got:
        w.host._sender(bid)._key("api.anthropic.com")
    assert got.value.message == sender.VAULT_LINE
    assert w.host._github_key(bid) == ({}, "")                     # searches go unkeyed, nothing is sent
    assert w.host.view(bid)["keys"] == {"cloud": True, "leased": False, "unlock": True}
    arch.unlock("pin", PIN)                                         # her unlock lets it go on
    assert w.host.agent_model(bid, {"probe": True}) == {"ok": True}
    assert len(_mine(arch, bid)) == 1


@pytest.mark.parametrize("goal,jobs", GOALS[1:], ids=["invoices", "birds"])
def test_closing_buildbot_ends_every_key_a_yes_lent(tmp_path, state, goal, jobs, monkeypatch):
    """Stop BuildBot (Ctrl+C, puffpai shutdown, Stop BuildBot.cmd) runs ``ToolsmithHost.stop``: every lease ends,
    even one taken at a Yes whose build has no process yet (store apps being added)."""
    w, arch, _clock = _world(tmp_path, state)
    running, cid = _planned(w, goal, jobs)
    _yes(w, running, cid)
    waiting, cid2 = _planned(w, goal + " too", jobs)
    _adding(w, monkeypatch)
    w.ports.consent.say_yes(cid2)                                   # store apps first: lent at the Yes, no process
    assert len(_mine(arch, running)) == 1 and len(_mine(arch, waiting)) == 1
    w.host.stop(grace_s=0)
    assert arch.leases() == []
    assert w.host.view(running)["keys"]["leased"] is False


# -- a Yes on a locked vault is one step -----------------------------------------------------

@pytest.mark.parametrize("goal,jobs", GOALS, ids=["recipes", "invoices", "birds"])
def test_a_yes_on_a_locked_vault_unlocks_and_lends_in_one_step(tmp_path, state, goal, jobs):
    w, arch, clock = _world(tmp_path, state)
    bid, cid = _planned(w, goal, jobs)
    _idle(arch, clock)                                              # it locked while she read the plan card
    assert w.host.view(bid)["keys"]["unlock"] is True              # so the card asks for her PIN with the Yes
    # The Desktop's one step (desktop.js keysReady): her usual unlock, then her Yes, then the start.
    arch.unlock("pin", PIN)
    w.ports.consent.say_yes(cid)
    w.host.start(bid, cid)
    assert len(_mine(arch, bid)) == 1                               # lent with her Yes
    _idle(arch, clock)                                              # the build runs on: it locks again
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY
    assert w.host.view(bid)["keys"]["unlock"] is False
    assert _unlocks(arch) == 1                                      # one unlock, with her Yes; none after it


def test_a_yes_with_no_pin_on_a_locked_vault_still_starts_and_waits_plainly(tmp_path, state):
    """A path with no PIN box (the terminal wizard) behaves as before: the build starts and waits (lane I18a)."""
    w, arch, clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[1])
    _idle(arch, clock)
    w.ports.consent.say_yes(cid)
    assert arch.leases() == []
    w.host.start(bid, cid)
    assert w.runner.runs == [("build", bid)]
    assert w.host.take_keys(bid) == "locked"
    assert w.host.view(bid)["keys"]["unlock"] is True              # the Building page asks for her PIN here
    arch.unlock("pin", PIN)
    assert w.host.agent_model(bid, {"probe": True}) == {"ok": True}
    _idle(arch, clock)
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY     # kept from then on


def test_a_no_takes_nothing_and_a_launch_that_fails_gives_the_keys_back(tmp_path, state):
    w, arch, _clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[0])
    w.ports.consent.say_yes(cid)
    w.host._launch_failed[bid] = "test"

    def fails(do, build_id, token, *, cache, on_exit):
        raise OSError("no process")
    w.runner.launch = fails
    w.host.start(bid, cid)
    assert arch.leases() == []                                      # it did not start: nothing stays lent


@pytest.mark.parametrize("goal,jobs", GOALS[:2], ids=["recipes", "invoices"])
def test_store_apps_added_first_keep_the_key_from_her_yes_through_the_idle_lock(tmp_path, state, goal, jobs,
                                                                                monkeypatch):
    w, arch, clock = _world(tmp_path, state)
    _adding(w, monkeypatch)
    bid, cid = _planned(w, goal, jobs)
    _idle(arch, clock)
    arch.unlock("pin", PIN)                                         # the one step: her PIN with her Yes
    w.ports.consent.say_yes(cid)
    assert len(_mine(arch, bid)) == 1 and len(_gh(arch)) == 1      # lent at her Yes: store apps come first
    _idle(arch, clock)                                              # the adds take a while: it locks again
    _added(w, bid, cid)
    assert w.runner.runs == [("build", bid)]
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY     # no second unlock
    assert _unlocks(arch) == 1


# -- keys exist only while the build may hold them (verifier round, lane I19a) ---------------------

def test_replace_after_her_yes_and_before_the_start_gives_the_keys_back(tmp_path, state, monkeypatch):
    w, arch, _c = _world(tmp_path, state)
    _adding(w, monkeypatch)
    bid, cid = _planned(w, *GOALS[0])
    w.ports.consent.say_yes(cid)
    assert len(_mine(arch, bid)) == 1
    assert w.host.replace(bid, "newer-build") is True
    assert arch.leases() == []
    assert w.host.take_keys(bid) == "" and arch.leases() == []      # a replaced build never borrows again


@pytest.mark.parametrize("covers", [lambda b: {"build": b["id"], "plan_sha": "bogus"},
                                    lambda b: {"build": b["id"], "plan_sha": b["plan_sha"]}],
                         ids=["wrong-sha", "right-sha-other-card"])
def test_a_yes_on_any_other_card_naming_the_build_lends_nothing(tmp_path, state, monkeypatch, covers):
    w, arch, _c = _world(tmp_path, state)
    _adding(w, monkeypatch)
    bid, _cid = _planned(w, *GOALS[1])
    b = w.host.store.read(bid)
    other = w.ports.consent_request("package", "some.other.package", covers(b))
    w.ports.consent.say_yes(other)
    assert arch.leases() == []


def test_safe_mode_ends_keys_lent_at_a_yes_with_no_process_yet(tmp_path, state, monkeypatch):
    w, arch, clock = _world(tmp_path, state)
    _adding(w, monkeypatch)
    bid, cid = _planned(w, *GOALS[2])
    w.ports.consent.say_yes(cid)
    assert len(_mine(arch, bid)) == 1
    _idle(arch, clock)                                              # already locked: Caretaker's lock is a no-op
    w.host.pause("test safe mode")
    assert arch.leases() == []
    assert w.host.take_keys(bid) == "" and arch.leases() == []      # paused: nothing is lent again


def test_a_safe_mode_vault_lock_ends_every_lease(tmp_path, state):
    w, arch, _c = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[0])
    _yes(w, bid, cid)
    assert len(_mine(arch, bid)) == 1
    arch.lock("safe_mode")                                          # Caretaker's own lock (caretaker.py lock_vault)
    assert arch.leases() == []


@pytest.mark.parametrize("adding", [False, True], ids=["plain", "store-apps"])
def test_a_start_refused_in_safe_mode_leaves_nothing_lent(tmp_path, state, monkeypatch, adding):
    w, arch, _c = _world(tmp_path, state)
    if adding:
        _adding(w, monkeypatch)
    bid, cid = _planned(w, *GOALS[0])
    w.ports.consent.say_yes(cid)
    w.host._paused = "x"
    with pytest.raises(service.Paused):
        w.host.start(bid, cid)
    assert arch.leases() == []


def test_a_start_with_someone_elses_yes_gives_nothing_and_ends_nothing_live(tmp_path, state):
    w, arch, _c = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[2])
    with pytest.raises(PermissionError):
        w.host.start(bid, "c_not_hers")
    assert arch.leases() == []
    _yes(w, bid, cid)
    w.host._children[bid] = type("Live", (), {"wait": lambda self, t=None: False, "stop": lambda self: None})()
    with pytest.raises(PermissionError):
        w.host.start(bid, "c_not_hers")                             # a stray start never ends a live run's keys
    assert len(_mine(arch, bid)) == 1
    w.host._children.pop(bid)


def test_a_no_on_the_plan_card_lends_nothing(tmp_path, state, monkeypatch):
    w, arch, _c = _world(tmp_path, state)
    _adding(w, monkeypatch)
    bid, cid = _planned(w, *GOALS[0])
    w.host._answered({"status": "declined", "consent_id": cid, "covers": {"build": bid, "plan_sha": "x"},
                      "scope": {"kind": "package", "target": store.bridge_target(bid)}})
    assert arch.leases() == []


# -- the 3 hour cap stays (X22) and is said plainly ----------------------------------------------

def test_the_3_hour_cap_stays_and_a_capped_build_waits_with_a_clear_line(tmp_path, state):
    assert archivist_mod.LEASE_MAX_S == 3 * 60 * 60                 # the floor's limit is unchanged
    w, arch, clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[1])
    _yes(w, bid, cid)
    _idle(arch, clock)
    clock.advance(archivist_mod.LEASE_MAX_S)
    with pytest.raises(CloudWaitVault) as got:
        w.host._sender(bid)._key("api.anthropic.com")
    assert got.value.message == sender.LEASE_CAP_LINE
    assert "3 hours" in sender.LEASE_CAP_LINE
    assert w.host.view(bid)["keys"]["unlock"] is True
    assert w.host._github_key(bid) == ({}, "")
    arch.unlock("pin", PIN)                                         # her unlock: a new lease, as before
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY


# -- "Check again" keeps its key for its run ---------------------------------------------------

def test_check_again_on_a_locked_vault_asks_first_then_keeps_the_key_for_the_run(tmp_path, state):
    w, arch, clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[2])
    _yes(w, bid, cid)

    def held(x):
        for j in x["jobs"]:
            j["run"].update(state="held", line="It stopped.")
    w.host.store.change(bid, held)
    w.host._child_done(bid, "build", 0)
    assert arch.leases() == []
    _idle(arch, clock)
    assert w.host.view(bid)["keys"]["unlock"] is True              # the Check again card asks for her PIN
    with pytest.raises(Refused) as got:
        w.host.again(bid)
    assert str(got.value) == service.AGAIN_VAULT_LINE and arch.leases() == []
    arch.unlock("pin", PIN)                                         # the one step: unlock, then Check again
    w.host.again(bid)
    assert w.runner.runs[-1] == ("build", bid) and len(_mine(arch, bid)) == 1
    _idle(arch, clock)                                              # the run outlives the idle lock
    assert w.host._sender(bid)._key("api.anthropic.com") == KEY
    w.host._child_done(bid, "build", 0)
    assert arch.leases() == []


def test_check_again_that_only_raises_a_card_gives_its_key_back(tmp_path, state):
    w, arch, _clock = _world(tmp_path, state)
    bid, cid = _planned(w, *GOALS[0])
    _yes(w, bid, cid)
    w.host.store.change(bid, lambda x: [j["run"].update(state="held") for j in x["jobs"]])
    w.host._child_done(bid, "build", 0)
    w.ports.consent.records.pop(cid)                                # her first Yes was taken back
    got = w.host.again(bid)
    assert got["again"]["state"] == "asking" and arch.leases() == []
    assert got["keys"]["leased"] is False                           # the view says so after it went back


# -- the plan card says it plainly -----------------------------------------------------------

def test_the_plan_card_says_what_her_yes_lends_and_for_how_long(tmp_path, state):
    w, _arch, _clock = _world(tmp_path, state)
    bid, _cid = _planned(w, *GOALS[1])
    lines = w.host.view(bid)["card"]["lines"]
    assert store.KEY_KEPT_LINE in lines
    assert store.KEY_KEPT_LINE == ("When you say Yes, Toolsmith keeps the key it needs until this build ends, you "
                                   "lock the vault, or you close BuildBot (3 hours at most).")
    assert "stop it" not in store.KEY_KEPT_LINE                     # there is no per-build Stop to promise
    local, _a, _c = _world(tmp_path / "local", state.parent / "state-local", cloud=False)
    lbid, _lcid = _planned(local, *GOALS[1])
    assert store.KEY_KEPT_LINE not in local.host.view(lbid)["card"]["lines"]
    for text in (store.KEY_KEPT_LINE, sender.LEASE_CAP_LINE):
        assert "\u2014" not in text


# -- the Desktop: one step on the card, Check again and a waiting build -------------------------

def test_the_desktop_unlocks_before_it_answers_and_never_loses_a_typed_pin():
    desk = (WEB / "desktop.js").read_text(encoding="utf-8")
    body = desk[desk.index("function consentEl("):desk.index("async function pendingConsents(")]
    assert body.index("keysReady(c.plan.build") < body.index('"/v1/consent/"')     # unlock first, then the answer
    ready = desk[desk.index("async function keysReady("):desk.index("async function pendingConsents(")]
    assert '"/v1/vault/unlock"' in ready and "L.buildKeys(r.data).unlock" in ready
    assert "afterPlanYes" not in ready                              # lane I19c's hunk is not touched
    page = (WEB / "pages" / "building.js").read_text(encoding="utf-8")
    assert "P.keysReady(v.id, unlockBox" in page                    # Check again asks in the same card
    assert "v.active && v.yes && v.unlock" in page and "L.UNLOCK_WAITING" in page
    assert "typingPin(sec)" in page                                 # the 4 s poll never wipes her PIN
    for src in (desk, page):
        assert "innerHTML" not in src and "\u2014" not in src


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_desktop_logic_reads_the_build_keys():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i19a.test.js")], capture_output=True, text=True,
                         timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
    for name in ("logic.js", "desktop.js", "pages/building.js"):
        chk = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert chk.returncode == 0, chk.stderr
