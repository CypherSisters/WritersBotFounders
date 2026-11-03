"""D143: on a cloud build, a slow local brain costs no job its search. From the owner's build on her Orin, where a plan ran
out of its time on a small model: three of six jobs were never searched, and the build ended with no apps.

The real ``agent.plan`` and ``find.Finder`` on the fake link of ``test_sources_plan_time``, every wait scaled down. Times
are plan seconds there."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from puffpai_core.wiring import WizardToolsmith
from puffpai_core.wizard import lines as L
from puffpai_core.zone3.toolsmith import find
from test_sources_plan_time import ALL, JOBS, LIMIT, Link, scaled  # noqa: F401 - scaled is a fixture

SLOW = 160.0                                           # plan seconds per ask: three asks use up the plan's share


class Plan(Link):
    """Keeps what was asked, in order. *cloud*: she chose a company's model. No company model is here to ask."""

    def __init__(self, brain_s, *, cloud=True, picks=None, store=()):
        super().__init__(brain_s, ALL)
        self.cloud, self.picks, self.store = cloud, picks or (lambda ask: ""), list(store)
        self.said: list[str] = []
        self.lock = threading.Lock()

    def _note(self, what):
        with self.lock:
            self.said.append(what)

    def build(self):
        got = super().build()
        if self.cloud:
            got["settings"]["where"] = "cloud"
        return got

    def registry(self):
        return {**super().registry(), "store": self.store}

    def brain_json(self, system, user):
        self._note(system.split("\n")[0][:40])
        return super().brain_json(system, user)

    def fits(self, package, ask):
        self._note("fits")
        self.asks += 1
        self._wait(self.brain_s)
        pid = self.picks(ask)
        return {"ok": bool(pid), "asked": True, "package": pid, "called": [f"{pid}.do"] if pid else [], "reason": "none"}

    def fits_store(self, ask):
        self._note("fits_store")
        pid = self.picks(ask)
        return {"ok": bool(pid), "asked": True, "package": pid, "called": [f"{pid}.do"] if pid else []}

    def http_get(self, url, **kw):
        if "api.github.com/search" in url:
            self._note("search")
        return super().http_get(url, **kw)

    def model_turn(self, *a, **kw):
        pytest.fail("nothing goes to a company model before her plan Yes")

    def searched(self):
        return [n for n, _d in JOBS if any("/search/" in u and n.lower() in u for u in self.web)]

    def asked(self, start):
        return [s for s in self.said if s.startswith(start)]


def _missed(link):
    return [j["name"] for j in link.plan["jobs"] if {find.OUT_OF_TIME_LINE, find.OUT_OF_TIME_CLOUD_LINE} & set(j["plan"]["notes"])]


def test_a_brain_that_uses_up_the_time_costs_a_cloud_plan_no_search(scaled):  # noqa: F811
    link = Plan(SLOW)
    took = scaled(link)
    assert link.plan and took < LIMIT
    assert link.searched() == [n for n, _d in JOBS] and _missed(link) == []
    assert all(j["plan"]["candidates"] for j in link.plan["jobs"])
    assert not link.asked("TASK=search_queries") and not link.asked("TASK=rank")


def test_the_same_brain_on_a_local_plan_keeps_its_cap(scaled):  # noqa: F811
    """The stub is slow enough to hit the cap: this is her report, on the plan that still works this way."""
    link = Plan(SLOW, cloud=False)
    took = scaled(link)
    assert link.plan and took < LIMIT
    assert len(_missed(link)) >= 3 and link.searched() == []
    assert len(link.asked("TASK=search_queries")) == 3


def test_a_cloud_plan_still_finds_what_she_already_has_after_the_searches(scaled):  # noqa: F811
    def picks(ask):
        ask = ask.lower()
        return "other" if "recipes" in ask else "shop" if "invoices" in ask else ""
    link = Plan(0.5, picks=picks, store=[{"id": "shop", "name": "Shop"}])
    scaled(link)
    plans = {j["name"]: j["plan"] for j in link.plan["jobs"]}
    assert plans["Recipes"]["reuse"] == "other" and plans["Recipes"]["candidates"] == []
    assert plans["Birds"]["candidates"] and not plans["Birds"]["reuse"]
    last_search = len(link.said) - 1 - link.said[::-1].index("search")
    assert last_search < link.said.index("fits") and last_search < link.said.index("fits_store")


def test_a_store_offer_comes_when_there_is_time(scaled):  # noqa: F811
    link = Plan(0.5, picks=lambda ask: "shop" if "invoices" in ask.lower() else "", store=[{"id": "shop", "name": "Shop"}])
    link.fits = lambda package, ask: {"ok": False, "asked": True, "package": "", "called": [], "reason": "none"}
    scaled(link)
    plans = {j["name"]: j["plan"] for j in link.plan["jobs"]}
    assert plans["Invoices"]["store"]["package"] == "shop" and plans["Invoices"]["candidates"]


def test_a_local_plan_asks_before_it_searches_as_it_did(scaled):  # noqa: F811
    link = Plan(0.5, cloud=False)
    scaled(link)
    assert link.said.index("fits") < link.said.index("search")
    assert link.said[0].startswith("TASK=search_queries") and link.asked("TASK=rank")


def test_a_plan_starts_with_the_vault_locked():
    """The plan's wait for the vault is gone (D143 a): a plan needs no key."""
    started = threading.Event()
    me = SimpleNamespace(host=lambda: SimpleNamespace(view=lambda build: {"now": ""}, settings=lambda: {"where": "cloud"},
                                                      ports=SimpleNamespace(vault_open=lambda: False)),
                         _lock=threading.Lock(), _planning={}, _plan_and_persona=lambda build: started.set())
    got = WizardToolsmith.plan_start(me, "b1")
    assert started.wait(5) and got.get("vault") is None
    assert not hasattr(L, "PLAN_NEEDS_KEY")
