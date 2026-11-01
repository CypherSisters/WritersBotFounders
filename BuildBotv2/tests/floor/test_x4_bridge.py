"""Floor X4 (default closed, only the owner opens, providers only narrow), X10 (floor-level cards),
X18 (never a lockout), X24 (policy edits close the Bridge), X28 (agents have no Bridge subject).
Plus D28(9): a grant covers one domain and its sub-hosts, never a public suffix."""

import json

import pytest

from puffpai_core.consent import ConsentRefused
from puffpai_core.zone3.policy import GrantRefused
from z3helpers import grant, manifest, zone


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def test_default_closed_everything_refused(state):
    z = zone(state)
    for subject in ("pkg:freshtable", "toolsmith:cloud", "core:update", "provider:adguard"):
        d = z.bridge.allowed(subject, "api.example.com")
        assert (d.verdict, d.by, d.reason_code) == ("deny", "floor", "bridge_closed")


def test_granted_host_passes_and_undeclared_host_is_refused(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    d = z.bridge.allowed("pkg:freshtable", "tracker.example.com")
    assert (d.verdict, d.reason_code) == ("deny", "not_on_list")
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com", port=8443).allowed
    assert not z.bridge.allowed("pkg:other", "api.example.com").allowed
    codes = [(line["host"], line["code"]) for line in z.bridge.ledger.read()]
    assert ("tracker.example.com", "not_on_list") in codes and ("api.example.com", "granted") in codes


def test_closing_or_switching_off_beats_any_grant(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    z.bridge.set_switch("pkg:freshtable", False)
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").reason_code == "switched_off"
    z.bridge.set_switch("pkg:freshtable", True)
    z.bridge.close()
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").reason_code == "bridge_closed"


def test_ip_literals_and_local_names_are_refused(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    for host in ("8.8.8.8", "127.0.0.1", "2130706433", "0x7f000001", "nas.local", "localhost"):
        assert not z.bridge.allowed("pkg:freshtable", host).allowed, host


def test_only_a_core_minted_yes_grants(state):
    z = zone(state)
    z.bridge.open()
    with pytest.raises(GrantRefused):
        z.bridge.policy.add_grant("pkg:freshtable", "api.example.com", 443, phase="run", consent={})
    forged = {"consent_id": "c_forged", "sha": "ab" * 32}
    z.bridge.policy.add_grant("pkg:freshtable", "api.example.com", 443, phase="run", consent=forged)
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").reason_code == "not_on_list"


def test_a_revoked_yes_closes_its_grants(state):
    z = zone(state)
    rec = grant(z, manifest(hosts=["api.example.com"]))
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    z.consents.revoke(rec["consent_id"])
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed


@pytest.mark.parametrize("domain", ["com", "co.uk", "github.io", "*.com", "*.example.com", "8.8.8.8", "nas.local"])
def test_wildcard_and_public_suffix_grants_are_refused(state, domain):
    z = zone(state)
    with pytest.raises(ValueError):
        z.bridge.package_card(manifest(), subhosts=[domain])


def test_one_domain_and_its_sub_hosts(state):
    z = zone(state)
    grant(z, manifest(), subhosts=["github.com"])
    assert z.bridge.allowed("pkg:freshtable", "github.com").allowed
    assert z.bridge.allowed("pkg:freshtable", "api.github.com").allowed
    assert not z.bridge.allowed("pkg:freshtable", "evilgithub.com").allowed
    assert not z.bridge.allowed("pkg:freshtable", "github.com.evil.net").allowed


def test_agents_never_reach_out_x28(state):
    z = zone(state)
    z.bridge.open()
    for agent in ("concierge", "steward", "archivist", "majordomo", "caretaker", "drawbridge"):
        with pytest.raises(GrantRefused):
            z.bridge.policy.set_switch(f"agent:{agent}", True)
        assert not z.bridge.allowed(f"agent:{agent}", "api.example.com").allowed


def test_toolsmith_cloud_only_while_building(state):
    z = zone(state)
    card = z.consents.ask("cloud", "toolsmith:cloud", {"hosts": ["api.anthropic.com"]})
    z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    z.bridge.open()
    # Lane I3b: a build grant lives only while Toolsmith's process works on a build (Z3 section 2).
    assert not z.bridge.allowed("toolsmith:cloud", "api.anthropic.com", phase="build").allowed
    z.bridge.open_build("20261022-210000-abcdef")
    assert z.bridge.allowed("toolsmith:cloud", "api.anthropic.com", phase="build").allowed
    assert not z.bridge.allowed("toolsmith:cloud", "api.anthropic.com", phase="run").allowed
    assert not z.bridge.allowed("toolsmith:cloud", "api.openai.com", phase="build").allowed
    z.bridge.end_build("20261022-210000-abcdef")
    assert not z.bridge.allowed("toolsmith:cloud", "api.anthropic.com", phase="build").allowed


def test_install_hosts_close_when_install_ends(state):
    z = zone(state)
    grant(z, manifest(install_hosts=["registry.npmjs.org"]))
    assert z.bridge.allowed("pkg:freshtable", "registry.npmjs.org", phase="install").allowed
    assert not z.bridge.allowed("pkg:freshtable", "registry.npmjs.org", phase="run").allowed
    z.bridge.end_install("freshtable")
    assert not z.bridge.allowed("pkg:freshtable", "registry.npmjs.org", phase="install").allowed


def test_providers_only_narrow_and_never_block_this_computer_x18(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    assert z.bridge.policy.add_block("api.example.com", "host", "provider:feed")
    d = z.bridge.allowed("pkg:freshtable", "api.example.com")
    assert (d.reason_code, d.rule) == ("blocked_by_provider", "provider:feed")
    for pattern in ("localhost", "127.0.0.1", "192.168.1.20", "pc.localhost"):
        assert z.bridge.policy.add_block(pattern, "host", "provider:feed") is False
    snap = z.bridge.policy.snapshot()
    assert "consent" not in json.dumps(snap) and snap["subjects"]["pkg:freshtable"]["allow"]


def test_policy_edited_outside_closes_the_bridge_x24(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    path = state / "settings" / "bridge" / "policy.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["body"]["grants"].append(dict(raw["body"]["grants"][0], host="evil.example.net"))
    path.write_text(json.dumps(raw), encoding="utf-8")
    d = z.bridge.allowed("pkg:freshtable", "evil.example.net")
    assert (d.verdict, d.reason_code) == ("deny", "bridge_closed")
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert z.bridge.posture()["damaged"] is True
    assert any(line["code"] == "policy_damaged" for line in z.bridge.ledger.read())
    z.bridge.open()                                # the owner checks and opens again: her last good copy
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert not z.bridge.allowed("pkg:freshtable", "evil.example.net").allowed


def test_missing_or_junk_policy_reads_closed(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    (state / "settings" / "bridge" / "policy.json").unlink()
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").reason_code == "bridge_closed"
    (state / "settings" / "bridge" / "policy.json").write_text("{junk", encoding="utf-8")
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").reason_code == "bridge_closed"


PIN = "pin-Zq7!walrus-never-stored"        # distinctive: no random id or hash can contain it


def test_floor_level_card_refuses_a_tap_x10(state, monkeypatch):
    import sqlite3
    from puffpai_core import seams as seams_mod
    monkeypatch.setattr(seams_mod, "verify_pin", lambda *_a: None)   # as if lane L3's PIN check were not here
    z = zone(state)
    card = z.consents.ask("package", "rawreader", {"hosts": []}, floor=True)
    with pytest.raises(ConsentRefused) as e:
        z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert e.value.code == "needs_pin"
    with pytest.raises(ConsentRefused) as e:               # no PIN check wired (lane L3): still a no
        z.consents.answer(card["consent_id"], "yes", "pin", PIN, card_sha=card["card_sha"])
    assert e.value.code == "no_pin_check" and e.value.status == 503
    z.consents.verify_pin = lambda pin: False
    with pytest.raises(ConsentRefused) as e:
        z.consents.answer(card["consent_id"], "yes", "pin", PIN, card_sha=card["card_sha"])
    assert e.value.code == "step_up_failed"
    with pytest.raises(ConsentRefused) as e:               # Windows Hello: refused until it is built
        z.consents.answer(card["consent_id"], "yes", "hello", card_sha=card["card_sha"])
    assert e.value.code == "hello_not_built"
    z.consents.verify_pin = lambda pin: pin == PIN
    rec = z.consents.answer(card["consent_id"], "yes", "pin", PIN, card_sha=card["card_sha"])
    assert rec["factor"] == "pin" and rec["status"] == "granted"
    assert "pin" not in rec and PIN not in rec.values()     # the field, not a substring of the file
    with sqlite3.connect(state / "consent.db") as db:
        cells = [c for row in db.execute("SELECT * FROM cards") for c in row]
    assert PIN not in cells and not any(isinstance(c, str) and PIN in c for c in cells)


def test_send_cards_are_always_floor_level(state):
    z = zone(state)
    card = z.consents.ask("send", "pkg:freshtable", bound={"payload_sha": "ab" * 32, "labels": ["person.phone"]})
    assert card["floor"] is True


def test_every_decision_is_published_by_the_core(state):
    z = zone(state)
    got = []
    z.bus.subscribe("egress.*", got.append, audience="core")
    grant(z, manifest(hosts=["api.example.com"]))
    z.bridge.allowed("pkg:freshtable", "api.example.com")
    z.bridge.allowed("pkg:freshtable", "nope.example.com")
    decisions = [m for m in got if m["type"] == "egress.decision"]
    assert [d["payload"]["verdict"] for d in decisions] == ["allow", "deny"]
    assert all(d["source"]["kind"] == "core" for d in decisions)
    assert all(d["payload"]["ledger_id"] for d in decisions)
