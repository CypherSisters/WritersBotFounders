"""Lane I14: a build started from chat whose plan has only knowledge topics (lane I11) keeps its Yes card open,
as the wizard's path does, so the knowledge is read after her Yes. An installed app's offer alone is still
closed (chat has no tick for it). No real network: GitHub finds nothing, the knowledge pair is recorded."""

import types

from test_i3a_proof_fixes import ChatConsent, Notices
from test_i11_knowledge import Web, _fake_world
from puffpai_core import wiring


def test_a_chat_plan_with_only_knowledge_keeps_its_card(tmp_path, state):
    w = _fake_world(tmp_path, state, Web(), topics=["knot tying", "sailing knots"])
    try:
        consent = ChatConsent(w.ports, "no")
        core = types.SimpleNamespace(consent=consent, notices=Notices())
        cards = []
        got = wiring.chat_toolsmith(core, w.app, wait_s=1)("teach me about sailing knots", {"notify": cards.append})
        b = w.host.store.all()[-1]
        assert b["knowledge_plan"]["topics"] == ["knot tying", "sailing knots"]
        assert not any(j["plan"].get("candidates") for j in b["jobs"])        # nothing to install
        assert cards, got                                                     # her card was shown in chat
        assert any(ln.startswith("Knowledge: Toolsmith reads about") for ln in cards[0]["card"]["lines"])
        assert got["error"] == wiring.CHAT_SAID_NO                            # her answer, not "found nothing"
        assert not any("found nothing" in str(x) for x in consent.expired)
    finally:
        w.host.stop(timeout=10)


def test_a_chat_plan_with_nothing_at_all_is_still_closed(tmp_path, state):
    w = _fake_world(tmp_path, state, Web(), topics=[])
    try:
        core = types.SimpleNamespace(consent=ChatConsent(w.ports, "no"), notices=Notices())
        cards = []
        got = wiring.chat_toolsmith(core, w.app, wait_s=1)("teach me about sailing knots", {"notify": cards.append})
        assert not cards and got["ok"] is False and got["error"].startswith(wiring.CHAT_NOTHING)
    finally:
        w.host.stop(timeout=10)


def test_a_chat_plan_with_only_a_store_app_keeps_its_card(tmp_path, state):
    from toolsmith_fakes import World
    w = World(tmp_path, state)                                   # FreshTable is in the store, not installed

    def picks(package, ask, store=None):
        hit = store is not None and any(x in ask.lower() for x in ("shopping", "milk"))
        return {"ok": hit, "asked": True, "package": "freshtable" if hit else "",
                "called": ["freshtable.add_shopping"] if hit else [], "reason": "" if hit else "none"}
    w.ports.brain_picks = picks
    try:
        core = types.SimpleNamespace(consent=ChatConsent(w.ports, "no"), notices=Notices())
        cards = []
        got = wiring.chat_toolsmith(core, w.app, wait_s=1)("keep a shopping list, put milk on it",
                                                          {"notify": cards.append})
        b = w.host.store.all()[-1]
        assert any(j["plan"].get("store") for j in b["jobs"]), b["jobs"]
        assert not any(j["plan"].get("candidates") or j["plan"].get("reuse") for j in b["jobs"])   # store only
        assert cards and got["error"] == wiring.CHAT_SAID_NO
    finally:
        w.host.stop(timeout=10)
