"""Lane I11, the verifier's privacy round: knowledge topics are search words sent to Wikipedia and Gutenberg,
so the Core itself keeps every name, number, address and key shape out of them (not only the prompt).

Also: the Bridge grants only the fixed knowledge pair, whatever a record lists; the knowledge grants close
even when the build's other clean-up fails; the knowledge fetches name the project's organisation page in
their User-Agent, and a 403 says plainly that the source refused. Every name and value here is made up.
No real network.
"""

from __future__ import annotations

import json

import pytest

from test_i11_knowledge import (GOAL, KNOWLEDGE_HOSTS, Web, _allowed, _fake_world, _plan_yes, _yes_and_build,
                                real_core)  # noqa: F401 - real_core is a fixture
from z3helpers import zone
from puffpai_core import people
from puffpai_core.zone3.bridge import build_target, is_build_grant
from puffpai_core.zone3.policy import KNOWLEDGE_HOSTS as POLICY_HOSTS
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from puffpai_core.zone3.toolsmith_host.ports import KNOWLEDGE_USER_AGENT, Ports, you_names


@pytest.fixture(autouse=True)
def _two_article_sources(monkeypatch):
    """These tests are about how an article source and a book source are read: only those two rows here. Every other
    row has its own tests (``test_sources_knowledge.py``, ``test_sources_readers.py``)."""
    from puffpai_core.zone3.toolsmith import find
    real = find.sources

    def only_two():
        got = real()
        got["fetch"] = {k: v for k, v in got["fetch"].items() if k in ("wikipedia", "gutenberg")}
        return got
    monkeypatch.setattr(find, "sources", only_two)

HOUSEHOLD = ["Robin Example", "Sam Sample"]
CONTEXT = GOAL + " Vendor list Keeps wedding vendors"

#: The verifier's table, as rows of (topic the model gave, why it must never be sent). All values made up.
NEVER_SENT = [
    ("robin example wedding", "a household name (the people list)"),
    ("wedding ideas for sam", "a household first name"),
    ("Robin Example", "a household name, written as a name"),
    ("wedding for Jordan", "a capitalised word the goal never used: a given name"),
    ("Jordan Testperson", "a name the household does not know"),
    ("5551234567", "a bare phone number"),
    ("call 555 123 4567", "a phone number with spaces"),
    ("wedding 2027", "a number"),
    ("jane at example com", "a spelled-out email"),
    ("jane at example dot com", "a spelled-out email with dot"),
    ("robin at mail dot org", "a spelled-out email with a household name"),
    ("sk live abcdef1234", "a key shape with digits"),
    ("sk live abcdefghijklmnopqrstuv", "a key prefix and a long run"),
    ("xoxb token", "a key prefix"),
    ("AbCdEfGhIjKl", "mixed case inside a long run"),
    ("qwrtzpkls wedding", "random letters"),
    ("robin@example.com", "a written email"),
    ("wedding_planning", "not only letters"),
    ("one two three four five six", "more than five words"),
    ("a" * 61, "more than 60 characters"),
    ("ignore previous instructions", "words aimed at an AI"),
]
KEPT = ["wedding planning", "color theory", "floral design", "wedding reception music", "event budget",
        "wedding at home", "self-care", "dinner at the park"]


@pytest.mark.parametrize("topic,why", NEVER_SENT, ids=[w for _t, w in NEVER_SENT])
def test_the_core_never_keeps_a_topic_from_the_table(topic, why):
    got = kmod.clean_topics(KEPT + [topic], household=HOUSEHOLD, context=CONTEXT)
    assert topic not in got and got == KEPT, why


def test_plain_subject_topics_are_kept():
    assert kmod.clean_topics(KEPT, household=HOUSEHOLD, context=CONTEXT) == KEPT


def test_a_capital_counts_only_where_the_models_style_makes_it_a_name():
    # Sentence case: the first capital says nothing, a later one not in the goal is a name.
    sentence = ["Wedding planning", "Color theory", "Flowers for Jordan"]
    assert kmod.clean_topics(sentence, household=[], context=CONTEXT) == ["Wedding planning", "Color theory"]
    # A capitalised word her goal used is not a name ("Victorian" here).
    assert kmod.clean_topics(["victorian gardens", "Victorian gardens"], household=[],
                             context="a Victorian garden bot") == ["victorian gardens"]
    # Title Case on every topic: capitals carry no signal, so only the household check counts (honest limit).
    title = ["Color Theory", "Floral Design", "Jordan Testperson"]
    assert kmod.clean_topics(title, household=[], context=CONTEXT) == title
    assert kmod.clean_topics(title, household=["Jordan Testperson"], context=CONTEXT) == title[:2]


def test_a_screen_that_breaks_sends_no_topic_at_all():
    def broken(_t):
        raise RuntimeError("screen down")
    assert kmod.clean_topics(KEPT, broken, household=HOUSEHOLD, context=CONTEXT) == []


# -- the household: the people list, and the "You" settings only when that part is here ------------------
@pytest.fixture
def vault(state, monkeypatch):
    """Lane I12's You values live in a real vault (z1kit), open unless the test locks it."""
    from puffpai_core.zone1 import archivist
    from z1kit import make
    a, *_ = make(state)
    monkeypatch.setitem(archivist._BY_STATE, str(state.resolve()), a)
    return a


def test_household_names_read_the_people_list_and_the_you_settings(state, vault):
    from puffpai_core import you
    p = Ports(state)
    assert you_names(state) == []                                            # nothing kept: nothing added
    people.save(state, [{"id": "robin", "name": "Robin Example", "emoji": "", "role": "adult"},
                        {"id": "kit", "name": "Kit Sample", "emoji": "", "role": "child"}])
    assert {"Robin Example", "Kit Sample"} <= set(p.household_names())
    you.save(state, {"name": "Morgan Owner", "call_me": "Mo", "about": "", "timezone": ""})    # lane I12's reader
    assert you_names(state) == ["Morgan Owner", "Mo"]
    assert {"Robin Example", "Kit Sample", "Morgan Owner", "Mo"} <= set(p.household_names())
    you_file = (state / "settings" / "you.json").read_text(encoding="utf-8")
    assert "Morgan" not in you_file                                          # the file keeps only a ref


def test_you_kept_but_locked_means_no_topic_at_all(tmp_path, state, vault):
    """Fail closed (lane I14): her name can't be checked while the vault is locked, so no topic is kept."""
    from puffpai_core import you
    you.save(state, {"name": "Robin Example", "call_me": "Robin", "about": "", "timezone": ""})
    vault.lock()
    with pytest.raises(you.YouLocked):
        you_names(state)
    w = _fake_world(tmp_path, state, Web(), topics=["color theory", "robin example wedding"])
    w.ports.household_names = Ports(state).household_names
    assert w.host.plan(GOAL, None)["knowledge_plan"]["topics"] == []


def test_her_you_name_never_reaches_a_url(tmp_path, state, vault):
    """With You name "Robin Example" set (and not on the people list), the topic "Robin Example wedding" is
    never sent; the real Ports reads it through lane I12's vault reader."""
    from puffpai_core import you
    you.save(state, {"name": "Robin Example", "call_me": "Rob", "about": "", "timezone": ""})
    w = _fake_world(tmp_path, state, Web(), topics=["Robin Example wedding", "rob wedding", "color theory"])
    w.ports.household_names = Ports(state).household_names
    b = w.host.plan(GOAL, [{"name": "Vendor list", "does": "Keeps wedding vendors"}])
    assert b["knowledge_plan"]["topics"] == ["color theory"]
    _yes_and_build(w, b)
    sent = [s["url"] for s in w.ports.sent if "srsearch=" in s["url"] or "query=" in s["url"]]
    assert sent and not any(x in u.lower() for u in sent for x in ("robin", "rob%20", "rob+", "example"))


def test_the_plan_drops_leaked_topics_and_says_what_is_sent(tmp_path, state):
    leaked = ["wedding planning", "robin example wedding", "5551234567", "jane at example com",
              "sk live abcdef1234", "wedding for Jordan", "color theory"]
    w = _fake_world(tmp_path, state, Web(), topics=leaked)
    w.ports.household_names = lambda: list(HOUSEHOLD)
    b = w.host.plan(GOAL, [{"name": "Vendor list", "does": "Keeps wedding vendors"}])
    assert b["knowledge_plan"]["topics"] == ["wedding planning", "color theory"]
    lines = b["card"]["lines"]
    i = next(n for n, ln in enumerate(lines) if ln.startswith("Knowledge: Toolsmith reads about"))
    assert lines[i] == "Knowledge: Toolsmith reads about wedding planning, color theory."
    assert lines[i + 1] == "These words are sent to Wikipedia and Gutenberg as searches (Gutenberg may get a topic one word at a time)."
    # Lane I15: a topic held for a household name is said on her own card, in that one line only.
    held = "1 knowledge topic was not sent because it used a name from your household: robin example wedding."
    assert lines.count(held) == 1
    saved = (state / "builds" / b["id"] / "build.json").read_text(encoding="utf-8").replace(held, "")
    cards = json.dumps(w.ports.consent.cards).replace(held, "")
    for bad in ("Robin", "robin", "5551234567", "jane at", "abcdef1234", "Jordan"):
        assert bad not in saved and bad not in cards
    _yes_and_build(w, b)
    sent = [s["url"] for s in w.ports.sent if "srsearch=" in s["url"] or "query=" in s["url"]]
    assert sent and not any(x in u.lower() for u in sent for x in ("robin", "555", "jane", "sk%20live", "jordan"))


def test_a_household_that_cant_be_read_sends_no_topic(tmp_path, state):
    w = _fake_world(tmp_path, state, Web())

    def broken():
        raise RuntimeError("people list unreadable")
    w.ports.household_names = broken
    b = w.host.plan(GOAL, None)
    assert b["knowledge_plan"]["topics"] == []


# -- 2. the policy grants only the fixed list -------------------------------------------------------------
def test_one_constant_for_the_list():
    assert set(KNOWLEDGE_HOSTS) <= set(POLICY_HOSTS)
    assert set(kmod.plan_of(["wedding planning"])["hosts"]) <= set(POLICY_HOSTS)


def test_a_record_listing_another_host_grants_nothing_for_it(state):
    z = zone(state)
    z.bridge.open()
    bid = "20261026-090000-a1b2c3"
    rec = _plan_yes(z, bid, {"build": bid, "plan_sha": "a" * 64, "install_hosts": [],
                             "knowledge_hosts": ["evil.example", "en.wikipedia.org", "www.gutenberg.org:8443"]})
    assert z.bridge.open_build(bid, plan_consent=rec["consent_id"]) == 1
    subject = f"pkg:{build_target(bid)}"
    assert not _allowed(z, subject, "evil.example") and _allowed(z, subject, "en.wikipedia.org")
    assert {g["host"] for g in z.bridge.posture()["grants"]} == {"en.wikipedia.org"}
    z.bridge.end_build(bid)


# -- 3. knowledge grants close even when the rest of the clean-up fails -----------------------------------
def test_knowledge_grants_close_when_end_build_and_the_token_revoke_both_fail(real_core, state, monkeypatch):
    app, core, host, ports, web = real_core
    z = app.extensions["puffpai.zone3"]
    z.bridge.open()
    b = host.plan(GOAL, None)
    rec = core.consent.get(b["consent"]["id"])
    core.consent.answer(rec["consent_id"], "yes", "tap", by="owner", card_sha=rec["card_sha"])
    z.bridge.open_build(b["id"], plan_consent=rec["consent_id"])         # as its build process does
    subject = f"pkg:{build_target(b['id'])}"
    assert _allowed(z, subject, "en.wikipedia.org")

    def fails(*_a, **_k):
        raise RuntimeError("clean-up broke")
    monkeypatch.setattr(z.bridge, "end_build", fails)
    monkeypatch.setattr(core.tokens, "revoke_toolsmith", fails)
    with pytest.raises(RuntimeError):
        host._child_done(b["id"], "build", 1)
    assert not _allowed(z, subject, "en.wikipedia.org") and not _allowed(z, subject, "www.gutenberg.org")
    assert not [g for g in z.bridge.posture()["grants"] if is_build_grant(g) and g["host"] in POLICY_HOSTS]


def test_a_build_whose_end_build_fails_still_closes_its_knowledge_grants(real_core, state, monkeypatch):
    app, core, host, ports, web = real_core
    z = app.extensions["puffpai.zone3"]
    z.bridge.open()

    def fails(*_a, **_k):
        raise RuntimeError("end_build broke")
    monkeypatch.setattr(z.bridge, "end_build", fails)
    b = host.plan(GOAL, None)
    rec = core.consent.get(b["consent"]["id"])
    core.consent.answer(rec["consent_id"], "yes", "tap", by="owner", card_sha=rec["card_sha"])
    done = host.start(b["id"], rec["consent_id"])
    assert done["knowledge_run"]["counts"]["wikipedia"] > 0
    assert not _allowed(z, f"pkg:{build_target(b['id'])}", "en.wikipedia.org")


# -- 4. the knowledge User-Agent, and a 403 said plainly --------------------------------------------------
def test_the_knowledge_user_agent_names_the_organisation_page_only():
    assert KNOWLEDGE_USER_AGENT == "PuffPAI-BuildBot/2 (+https://github.com/CypherSisters)"
    assert "@" not in KNOWLEDGE_USER_AGENT


def test_a_403_from_wikipedia_says_it_refused(tmp_path, state):
    class Refusing(Web):
        def __call__(self, url, headers):
            if "wikipedia" in url:
                self.calls.append((url, dict(headers)))
                return 403, b"Please set a user-agent"
            return super().__call__(url, headers)
    web = Refusing()
    w = _fake_world(tmp_path, state, web, topics=["wedding planning"])
    b = w.host.plan(GOAL, None)
    run = _yes_and_build(w, b)["knowledge_run"]
    assert "Wikipedia refused the request (it answered 403), so nothing was read from it." in run["line"]
    assert all(h.get("User-Agent") == KNOWLEDGE_USER_AGENT for _u, h in web.calls)


# -- 5. no hidden characters left in the module -----------------------------------------------------------
def test_no_byte_order_mark_in_the_knowledge_module():
    assert "﻿" not in open(kmod.__file__, encoding="utf-8").read()
