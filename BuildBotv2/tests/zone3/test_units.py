"""Small Zone 3 units: host rules, chain rotation, card expiry."""

from datetime import timedelta

import pytest

from puffpai_core.consent import store as consent_mod
from puffpai_core.zone3 import hosts
from puffpai_core.zone3.chain import ChainLog, verify
from puffpai_core.consent import ConsentRefused, ConsentStore


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


@pytest.mark.parametrize("host, ok", [("api.example.com", True), ("API.Example.com.", True), ("xn--bcher-kva.de", True),
                                      ("com", False), ("localhost", False), ("nas.local", False),
                                      ("router.home.arpa", False), ("1.2.3.4", False), ("0x7f.1", False),
                                      ("127.1", False), ("[::1]", False), ("a_b.example.com", False),
                                      ("example.123", False), ("*.example.com", False)])
def test_dns_names(host, ok):
    assert hosts.is_dns_name(host) is ok


def test_lan_addresses():
    assert hosts.is_private_address("192.168.1.20") and hosts.is_private_address("10.0.0.5")
    for addr in ("127.0.0.1", "0.0.0.0", "8.8.8.8", "169.254.1.1", "224.0.0.1"):
        assert not hosts.is_private_address(addr), addr
    assert hosts.is_public("93.184.216.34") and not hosts.is_public("100.64.1.1")


def test_split_hostport():
    assert hosts.split_hostport("api.example.com") == ("api.example.com", 443)
    assert hosts.split_hostport("api.example.com:8443") == ("api.example.com", 8443)
    assert hosts.split_hostport("[fe80::1]:80") == ("fe80::1", 80)
    with pytest.raises(ValueError):
        hosts.split_hostport("api.example.com:0")


def test_chain_rotates_and_goes_on(tmp_path):
    log = ChainLog(tmp_path / "outside.jsonl", max_bytes=600)
    for i in range(20):
        log.append({"action": "x", "i": i})
    old = tmp_path / "outside.1.jsonl"
    assert old.exists() and verify(log.path) == (True, 0) and verify(old) == (True, 0)
    first = log.read(2000)[-1]
    assert first["event"] == "chain_rotated" and first["n"] == 1 and first["prev"] == "0" * 64
    old_last = [ln for ln in old.read_text(encoding="ascii").splitlines() if ln][-1]
    import json
    assert (first["from_n"], first["from_h"]) == (json.loads(old_last)["n"], json.loads(old_last)["h"])
    again = ChainLog(tmp_path / "outside.jsonl", max_bytes=600)     # reopens whole: sealed head and old file match
    assert not again.tampered and again.head() == log.head()


def test_chain_reopens_where_it_left_off(tmp_path):
    log = ChainLog(tmp_path / "a.jsonl")
    log.append({"action": "x"})
    head = log.head()
    again = ChainLog(tmp_path / "a.jsonl")
    assert again.head() == head and not again.tampered


def test_cards_expire(tmp_path, monkeypatch):
    store = ConsentStore(tmp_path / "consent.db")
    heard = []
    store.on_answer(heard.append)
    card = store.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    real = consent_mod._now
    monkeypatch.setattr(consent_mod, "_now", lambda: real() + timedelta(minutes=11))
    with pytest.raises(ConsentRefused) as e:
        store.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert e.value.code == "expired"
    assert [r["status"] for r in heard] == ["expired"]       # listeners hear it like a No


def test_an_edited_consent_record_is_not_a_yes(tmp_path):
    import sqlite3
    store = ConsentStore(tmp_path / "consent.db")
    card = store.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    store.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert store.granted(card["consent_id"])
    with sqlite3.connect(tmp_path / "consent.db") as db:          # a same-user edit of the file
        db.execute("UPDATE cards SET covers = replace(covers, 'api.github.com', 'evil.example.net')")
    again = ConsentStore(tmp_path / "consent.db")
    assert again.live() == [] and again.get(card["consent_id"])["status"] in ("void", "damaged")
