"""Lane I16: the Core adds the PuffPAI store apps ticked on a plan card, then starts the build, for every path.

The Toolsmith host's ``store_phase`` raises each app's Package Manager Yes card, waits until the card is
answered AND the install reaches a final status (never "installing"; "broken" counts as failed), and only then
starts the build. ``start`` asked for meanwhile (from the Desktop, the chat or the wizard) starts nothing, so it
stays safe to call twice. Every "not added" line is true. No real install: the Package Manager, its cards and
the build's process are recorded fakes; the Registry is real.
"""

from __future__ import annotations

import threading
import time
import types

import pytest

from conftest import FIXTURES
from test_i10c_name_theme import SHOP, _store_plan
from toolsmith_fakes import FakePorts, World, _Done
from puffpai_core import wiring
from puffpai_core.registry import row_from_manifest
from puffpai_core.zone3.toolsmith_host import store_phase as sp


class StorePorts(FakePorts):
    """The Package Manager's store add, its card's answer, and the install result, all set by the test."""

    def __init__(self, tmp):
        super().__init__(tmp)
        self.store_cards = {}                     # card id -> status (pending, granted, declined, expired)
        self.results = {}                         # card id -> the install's result, once it is final
        self.release = threading.Event()
        self.release.set()
        self.store_added = []
        self.expired_cards = []

    def packages_add(self, folder, plan=None, adopted=None):
        if str(folder) != "freshtable":
            return super().packages_add(folder, plan, adopted)
        cid = f"pm_store_{len(self.store_cards) + 1}"
        self.store_cards[cid] = "pending"
        self.store_added.append(cid)
        return {"consent_id": cid, "id": "freshtable", "card": {"title": "Add FreshTable?"}, "card_sha": "s",
                "floor": False}

    def consent_state(self, cid):
        if cid in self.store_cards:
            return {"status": self.store_cards[cid]}
        rec = self.consent.get(cid)
        return {"status": "granted" if rec else "pending"}

    def consent_expire(self, cid, note=""):
        self.expired_cards.append((cid, note))
        if self.store_cards.get(cid) == "pending":
            self.store_cards[cid] = "expired"

    def packages_wait(self, cid, timeout_s=0.0):
        if cid not in self.store_cards:
            return super().packages_wait(cid, timeout_s)
        self.release.wait(timeout_s or 30)
        return dict(self.results.get(cid, {"status": "declined"}))


class Launches:
    """Toolsmith's build process, recorded: when it was launched, and what the store app's row said then.
    Planning runs for real (inline), so the chat can make its plan."""

    def __init__(self, registry, inline):
        self.registry, self.inline, self.runs = registry, inline, []

    def launch(self, do, build_id, token, *, cache, on_exit):
        if do == "plan":
            return self.inline.launch(do, build_id, token, cache=cache, on_exit=on_exit)
        row = self.registry.get("freshtable")
        self.runs.append((do, build_id, (row or {}).get("status")))
        on_exit(0)
        return _Done(0)


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state, ports=StorePorts(tmp_path))
    w.host.store_phase.poll_s = 0.02
    w.ports.brain_picks = lambda package, ask, store=None: {"ok": True, "asked": True, "package": "freshtable",
                                                            "called": ["freshtable.add_shopping"], "reason": ""}
    w.launches = Launches(w.registry, w.runner)
    w.host.runner = w.launches
    yield w
    w.ports.release.set()
    w.host.stop(timeout=10)


def _freshtable():
    import json
    return json.loads((FIXTURES / "freshtable" / "puffpai.json").read_text(encoding="utf-8"))


def _ticked_yes(w, tick=True):
    """A plan with FreshTable's store offer, ticked (or not), and the plan's Yes."""
    b = _store_plan(w)
    cid = b["consent"]["id"]
    if tick:
        w.host.use_offers(b["id"], cid, [0])
    w.ports.consent.say_yes(cid)                  # the Yes listener runs: the Core begins the store adds
    return b, cid


def _until(fn, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.02)
    return fn()


def _app(w, b):
    return ((w.host.view(b["id"]).get("store_adds") or {}).get("apps") or [{}])[0]


def _done(w, b):
    return (w.host.view(b["id"]).get("store_adds") or {}).get("state") == "done"


def test_yes_then_install_then_the_build_starts_only_when_the_install_is_final(world):
    w = world
    w.ports.release.clear()                                            # the install takes its time
    b, cid = _ticked_yes(w)
    assert _until(lambda: _app(w, b).get("state") == "asking"), str(_app(w, b))
    assert w.launches.runs == []
    again = w.host.start(b["id"], cid)                                 # the Desktop or wizard asks too: nothing yet
    assert not again.get("yes") and w.launches.runs == []
    card = _app(w, b)["card"]
    w.ports.store_cards[card] = "granted"                              # her Yes on FreshTable's own card
    assert _until(lambda: _app(w, b).get("state") == "installing")
    w.registry.add(row_from_manifest(_freshtable(), "local:freshtable", status="installing"))
    w.ports.results[card] = {"status": "installed", "id": "freshtable"}
    w.ports.release.set()                                              # the Package Manager says installed...
    time.sleep(0.4)
    assert w.launches.runs == [] and not w.host.view(b["id"]).get("yes")    # ...but its row still says installing
    w.registry.update("freshtable", status="running")
    assert _until(lambda: w.launches.runs)
    assert w.launches.runs == [("build", b["id"], "running")]          # started once, after the final status
    assert _until(lambda: _done(w, b))
    v = w.host.view(b["id"])
    assert _app(w, b)["line"] == "Added FreshTable from the PuffPAI store."
    assert v["store_adds"]["line"] == sp.STARTED
    assert v["store_adds"]["said"] == [sp.ASKING.format(name="FreshTable"), sp.INSTALLING.format(name="FreshTable"),
                                       sp.ADDED.format(name="FreshTable"), sp.STARTED]     # in order, only growing
    assert v["jobs"][0]["plan"]["reuse"] == "freshtable" and v["jobs"][0]["plan"]["reuse_by"] == "store"
    w.host.start(b["id"], cid)                                         # a second start changes nothing
    assert len(w.launches.runs) == 1


@pytest.mark.parametrize("how,line", [
    ("no", "FreshTable was not added, so its job has nothing to use. " + sp.LATER),
    ("expired", "FreshTable was not added: its Yes card got no answer in time. " + sp.LATER),
    ("failed", "FreshTable was not added: Drawbridge said no to it. " + sp.LATER),
    ("broken", "FreshTable was not added: it was installed but does not work (its server did not answer). " + sp.LATER),
])
def test_each_way_it_is_not_added_has_a_true_line_and_the_build_still_starts(world, how, line):
    w = world
    b, cid = _ticked_yes(w)
    assert _until(lambda: _app(w, b).get("state") == "asking")
    card = _app(w, b)["card"]
    if how == "no":
        w.ports.store_cards[card] = "declined"
    elif how == "expired":
        w.ports.store_cards[card] = "expired"
    elif how == "failed":
        w.ports.results[card] = {"status": "failed", "problems": ["Drawbridge said no to it."]}
        w.ports.store_cards[card] = "granted"
    else:
        row = row_from_manifest(_freshtable(), "local:freshtable", status="broken")
        w.registry.add(row)
        w.registry.set_health("freshtable", False, "its server did not answer")
        w.ports.results[card] = {"status": "installed", "id": "freshtable"}
        w.ports.store_cards[card] = "granted"
    assert _until(lambda: _done(w, b)), w.host.view(b["id"]).get("store_adds")
    assert _app(w, b)["state"] == "not_added" and _app(w, b)["line"] == line
    assert [r[:2] for r in w.launches.runs] == [("build", b["id"])]


def test_a_card_nobody_answers_is_closed_with_its_reason(world, monkeypatch):
    w = world
    monkeypatch.setattr(sp, "CARD_WAIT_S", 0.3)
    b, cid = _ticked_yes(w)
    assert _until(lambda: _done(w, b))
    assert _app(w, b)["line"].startswith("FreshTable was not added: its Yes card got no answer in time.")
    assert w.ports.expired_cards and w.ports.expired_cards[0][0] == _app(w, b)["card"]
    assert len(w.launches.runs) == 1


def test_nothing_ticked_means_no_store_adds_and_start_is_immediate(world):
    w = world
    b, cid = _ticked_yes(w, tick=False)
    assert w.host.view(b["id"]).get("store_adds") is None
    assert w.launches.runs == []                                       # the Yes alone starts nothing here
    w.host.start(b["id"], cid)
    assert len(w.launches.runs) == 1 and w.ports.store_added == []


def test_a_reload_mid_wait_still_ends_in_a_started_build(world):
    """The page that asked is gone (a reload): nothing on its side carries the wait. The Core's record is
    what any page reads again, and the build starts when the card is answered."""
    w = world
    b, cid = _ticked_yes(w)
    assert _until(lambda: _app(w, b).get("state") == "asking")
    seen = w.host.view(b["id"])["store_adds"]                          # what a reloaded page reads
    assert seen["state"] == "adding" and seen["apps"][0]["card"]
    w.ports.store_cards[seen["apps"][0]["card"]] = "declined"           # answered on Your turn, after the reload
    assert _until(lambda: w.launches.runs)
    assert _until(lambda: _done(w, b))


def test_a_core_that_restarted_mid_wait_goes_on_where_it_was(world):
    w = world
    w.ports.release.clear()
    b, cid = _ticked_yes(w)
    assert _until(lambda: _app(w, b).get("state") == "asking")
    card = _app(w, b)["card"]
    w.host.store_phase._threads.clear()                                # as after a restart: no thread for it
    w.ports.store_cards[card] = "declined"
    w.host.start(b["id"], cid)                                         # any start picks it up again, once
    w.ports.release.set()
    assert _until(lambda: _done(w, b))
    assert len(w.launches.runs) == 1


def test_many_callers_on_a_restarted_core_launch_the_build_once_and_done_comes_after(world):
    """The store phase has one owner per build. A Core restarted mid-wait (a new host on the same bot folder,
    while the old one's thread still waits on the card): resume, the plan Yes listener and 10 starts, all at
    once from threads. The build launches exactly once, and "done" is never seen before that launch."""
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    w = world
    w.ports.release.clear()
    b, cid = _ticked_yes(w)
    assert _until(lambda: _app(w, b).get("state") == "asking")
    card = _app(w, b)["card"]
    new = ToolsmithHost(w.state, w.ports, w.registry, w.core.tokens, runner=w.launches, sleep=lambda s: None)
    new.store_phase.poll_s = 0.02
    seen_early, stop = [], threading.Event()

    def watch():
        while not stop.is_set():
            rec = (w.host.store.read(b["id"]) or {}).get("store_adds") or {}
            if rec.get("state") == "done" and not w.launches.runs:
                seen_early.append(rec)
            time.sleep(0.001)
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    granted = dict(w.ports.consent.records[cid])
    gate = threading.Barrier(12)
    errors = []

    def call(fn):
        try:
            gate.wait(10)
            fn()
        except Exception as exc:  # noqa: BLE001 - every caller's error is a failure
            errors.append(repr(exc))
    callers = [threading.Thread(target=call, args=(new.store_phase.resume,)),
               threading.Thread(target=call, args=(lambda: new._granted(granted),))]
    callers += [threading.Thread(target=call, args=(lambda: new.start(b["id"], cid),)) for _ in range(10)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(10)
    assert errors == [] and w.launches.runs == []                      # the card still waits: nothing started
    w.ports.store_cards[card] = "declined"
    w.ports.release.set()
    assert _until(lambda: _done(w, b))
    time.sleep(0.5)                                                    # a stale thread would act by now
    stop.set()
    watcher.join(5)
    assert [r[0] for r in w.launches.runs] == ["build"], w.launches.runs
    assert seen_early == []
    said = w.host.view(b["id"])["store_adds"]["said"]
    assert said.count(sp.STARTED) == 1 and said[-1] == sp.STARTED, said
    new.stop(timeout=10)


def test_a_chat_raised_card_with_a_ticked_store_app(world):
    """The chat waits for the plan's Yes, then asks to start: the store app is added first, the Core starts the
    build after it, and the chat's watcher waits through the adds."""
    from test_i3a_proof_fixes import ChatConsent, Notices
    w = world
    consent = ChatConsent(w.ports, "yes")
    plan = {}
    real_wait = consent.wait

    def wait(cid, timeout_s=None):                                     # the tick comes in on the Desktop first
        b = w.host.store.all()[-1]
        plan["build"] = b["id"]
        w.host.use_offers(b["id"], cid, [0])
        return real_wait(cid, timeout_s)
    consent.wait = wait
    core = types.SimpleNamespace(consent=consent, notices=Notices())
    said_later = []
    got = wiring.chat_toolsmith(core, w.app, wait_s=5, every_s=0.05)(
        "keep a shopping list, put milk on it", {"notify": lambda c: None, "say_later": said_later.append})
    assert got["ok"] is True, got
    bid = plan["build"]
    b = {"id": bid}
    assert _until(lambda: _app(w, b).get("state") == "asking"), w.host.view(bid).get("store_adds")
    assert w.launches.runs == []                                       # the chat's start waited
    w.ports.store_cards[_app(w, b)["card"]] = "declined"
    assert _until(lambda: w.launches.runs)
    assert [r[:2] for r in w.launches.runs] == [("build", bid)]
    assert _app(w, b)["line"].startswith("FreshTable was not added, so its job has nothing to use.")


def test_the_wizard_shows_the_cores_store_card_and_its_lines():
    """The wizard's Toolsmith adapter: the store app's card is one of the build's cards, kind "store", and the
    adds' lines are one more line of the build's progress."""
    class Host:
        def __init__(self):
            said = ["FreshTable: its own Yes card is waiting. Your Yes adds it to your bot before the build."]
            self.store_phase = types.SimpleNamespace(record=lambda build: {"state": "adding", "line": "", "said": said, "apps": [
                {"package": "freshtable", "name": "FreshTable", "state": "asking", "card": "pm_1",
                 "line": "FreshTable: its own Yes card is waiting. Your Yes adds it to your bot before the build."}]})
            self.trials = types.SimpleNamespace(hold_cards=lambda b: [], of_build=lambda b: [])

        def view(self, build):
            return {"jobs": [], "yes": None}

        def running(self, build):
            return False

        def steps_of(self, build):
            return []

    consent = types.SimpleNamespace(get=lambda cid: {"status": "pending", "card": {"title": "Add FreshTable?", "name": "FreshTable"},
                                                     "floor": False})
    class Adapter(wiring.WizardToolsmith):
        consent = None                                                 # a plain attribute here, set below

    ts = Adapter.__new__(Adapter)
    ts.consent = consent
    ts.host = lambda: Host()
    got = ts.progress("b1")
    assert got["cards"] and got["cards"][0]["consent_id"] == "pm_1" and got["cards"][0]["kind"] == "store"
    assert got["jobs"][0]["name"] == "PuffPAI store" and "its own Yes card is waiting" in got["jobs"][0]["lines"][0]
    assert got["finished"] is False


def _cut_off_mid_wait(w):
    """A plan Yes with FreshTable ticked, its card granted and the app running, recorded as a Core that stopped
    mid-wait leaves it: store_adds "adding", the app "installing", and no thread anywhere."""
    b = _store_plan(w)
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, [0])
    w.ports.consent.records[cid] = {**w.ports.consent.cards[cid], "factor": "tap", "by": "owner"}   # her Yes, no listener
    w.host.store.fix_use(b["id"], cid)
    w.ports.store_cards["pm_store_1"] = "granted"
    w.ports.results["pm_store_1"] = {"status": "installed", "id": "freshtable"}
    w.registry.add(row_from_manifest(_freshtable(), "local:freshtable", status="running"))
    app = {"package": "freshtable", "name": "FreshTable", "job_name": "Shopping", "state": "installing",
           "card": "pm_store_1", "line": sp.INSTALLING.format(name="FreshTable")}
    w.host.store.change(b["id"], lambda x: x.update(store_adds={
        "consent_id": cid, "state": "adding", "apps": [app], "line": "",
        "said": [sp.ASKING.format(name="FreshTable"), sp.INSTALLING.format(name="FreshTable")]}))
    return b, cid


def test_a_core_restart_resumes_the_store_adds_and_launches_the_build_once(world, monkeypatch):
    from puffpai_core.zone3.toolsmith_host import ToolsmithHost
    w = world
    b, cid = _cut_off_mid_wait(w)
    starts = []
    real_start = ToolsmithHost.start
    monkeypatch.setattr(ToolsmithHost, "start", lambda self, *a, **k: (starts.append(a), real_start(self, *a, **k))[1])
    launches = Launches(w.registry, w.runner)
    again = ToolsmithHost(w.state, w.ports, w.registry, w.core.tokens, runner=launches, sleep=lambda s: None)
    again.store_phase.poll_s = 0.02
    try:
        assert again.resume_store_adds() == [b["id"]]                  # what the Core start does
        assert _until(lambda: (again.view(b["id"]).get("store_adds") or {}).get("state") == "done")
        time.sleep(0.3)
        assert launches.runs == [("build", b["id"], "running")]        # launched exactly once
        assert starts == []                                            # with no start() from any page or chat
        rec = again.view(b["id"])["store_adds"]
        assert rec["line"] == sp.STARTED and rec["apps"][0]["state"] == "added"
        assert again.resume_store_adds() == [] and len(launches.runs) == 1   # a second Core start: nothing again
    finally:
        again.stop(timeout=10)


def test_core_start_resumes_the_store_adds_unless_in_safe_mode(core, monkeypatch):
    from puffpai_core.api import app as app_mod
    calls = []

    class Host:
        def resume_store_adds(self):
            calls.append("resume")
            return []

        def pause(self, reason=""):
            calls.append("pause")
            return ""

        def stop(self, *a, **k):
            pass
    monkeypatch.setattr(app_mod, "start_toolsmith", lambda app: Host())
    terminal = app_mod.start_core(core)
    try:
        assert calls == ["pause"] if core.caretaker.in_safe_mode else calls == ["resume"]
    finally:
        app_mod.stop_core(core, terminal)
