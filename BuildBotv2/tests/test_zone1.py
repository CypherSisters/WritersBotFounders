"""Zone 1 (lane L3): vault routes, delete while locked, unattended after a restart, data and linked folders."""

import hashlib

import pytest

from conftest import FakeMachine
from puffpai_core.api import create_app
from puffpai_core.bus import Bus
from puffpai_core.zone1.archivist import Archivist
from puffpai_core.zone1.folders import FolderRefused, Library
from z1kit import PIN, canary_key, make


@pytest.fixture
def api(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    return app.test_client(), dev, owner


def test_vault_routes_end_to_end(api):
    client, dev, owner = api
    assert client.get("/v1/vault/status", headers=owner).json["state"] == "not_set_up"
    assert client.post("/v1/vault/setup", json={"pin": "12345678"}, headers=dev).status_code == 400
    r = client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    assert r.status_code == 201 and len(r.json["recovery_code"]) == 29
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 409
    st = client.get("/v1/vault/status", headers=owner).json
    assert st["state"] == "open" and st["ways_in"] == ["PIN", "Recovery code"]
    assert client.put("/v1/vault/secrets/MY_KEY", json={"value": canary_key()}, headers=owner).status_code == 201
    assert client.put("/v1/vault/secrets/bad-name", json={"value": "x"}, headers=owner).status_code == 400
    assert client.post("/v1/vault/lock", headers=owner).json == {"state": "locked"}
    r = client.put("/v1/vault/secrets/LATER_KEY", json={"value": canary_key()}, headers=owner)
    assert r.status_code == 423 and r.json["can_seal"] is True
    r = client.put("/v1/vault/secrets/LATER_KEY", json={"value": canary_key(), "seal_if_locked": True},
                   headers=owner)
    assert r.status_code == 202 and r.json["kept"] == "inbox"
    assert client.get("/v1/vault/status", headers=owner).json["inbox_waiting"] == 1
    r = client.post("/v1/vault/unlock", json={"method": "pin", "secret": "Wrong#pin99"}, headers=dev)
    assert r.status_code == 401
    assert client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev).status_code == 200
    assert client.get("/v1/vault/status", headers=owner).json["items"] == 2
    ways = client.get("/v1/vault/ways-in", headers=owner).json["ways_in"]
    assert client.delete("/v1/vault/ways-in/recovery", json={"pin": PIN}, headers=dev).status_code == 409
    assert client.delete("/v1/vault/ways-in/pin", json={"pin": PIN}, headers=dev).status_code == 409
    r = client.post("/v1/vault/ways-in", json={"kind": "passphrase", "label": "Spare",
                                               "secret": "violet kettle under stairs", "pin": PIN}, headers=dev)
    assert r.status_code == 201
    assert client.delete(f"/v1/vault/ways-in/{r.json['id']}", json={"pin": PIN}, headers=dev).status_code == 200
    assert len(client.get("/v1/vault/ways-in", headers=owner).json["ways_in"]) == len(ways)
    assert client.delete("/v1/vault/secrets/MY_KEY", headers=owner).status_code == 200


def test_throttle_answers_429_with_retry_after(api):
    client, dev, _ = api
    client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev)
    client.post("/v1/vault/lock", headers=dev)
    codes = [client.post("/v1/vault/unlock", json={"method": "pin", "secret": "Wrong#pin99"}, headers=dev)
             for _ in range(7)]
    assert codes[-1].status_code == 429 and int(codes[-1].headers["Retry-After"]) > 0


def test_delete_works_while_locked(state):
    a, *_ = make(state)
    a.put_secret("OLD_KEY", canary_key())
    a.lock()
    assert a.delete_secret("OLD_KEY") is True
    a.unlock("pin", PIN)
    assert [i["name"] for i in a.vault.items()] == []
    assert list(a.vault.store.folder.glob(a.vault._blob_id("OLD_KEY") + ".blob")) == []


def test_ticked_key_works_after_a_restart(state):
    machine = FakeMachine()
    a = Archivist(state, bus=Bus(), machine=machine)
    a.setup(PIN)
    key = canary_key()
    a.put_secret("HASS_TOKEN", key, unattended=True)
    a.put_secret("OTHER_TOKEN", canary_key())
    del a                                                     # a restart: a fresh Core, locked
    b = Archivist(state, bus=Bus(), machine=machine)
    assert not b.is_open()
    assert b.inject("hass", ["HASS_TOKEN", "OTHER_TOKEN"]) == {"HASS_TOKEN": key}
    b.delete_secret("HASS_TOKEN")                             # off is one step, even locked
    assert b.inject("hass", ["HASS_TOKEN"]) == {}


def test_data_and_linked_folders(state, tmp_path):
    a, *_ = make(state)
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    (lib.data_folder / "recipes.md").write_text("Tomato soup with basil and garlic.", encoding="utf-8")
    mine = tmp_path / "Documents"
    mine.mkdir()
    secret_doc = mine / "tax.txt"
    secret_doc.write_text("Tax notes. SSN 219-09-9999. Account number 12345678901. Refund pending.",
                          encoding="utf-8")
    before = hashlib.sha256(secret_doc.read_bytes()).hexdigest()
    mtime = secret_doc.stat().st_mtime
    lib.link(mine)
    report = lib.scan()
    assert report["indexed"] == 2 and report["held"] >= 2
    assert hashlib.sha256(secret_doc.read_bytes()).hexdigest() == before        # original untouched
    assert secret_doc.stat().st_mtime == mtime
    hits = lib.search("refund")
    assert hits and "219-09-9999" not in hits[0]["snippet"]
    assert lib.search("219") == [] and lib.search("12345678901") == []
    db = (state / "index" / "knowledge.db").read_bytes()
    assert b"219-09-9999" not in db and b"12345678901" not in db
    assert lib.search("tomato")
    assert lib.scan()["unchanged"] == 2
    with pytest.raises(FolderRefused):
        lib.link(state)
    assert lib.unlink(mine) and lib.search("refund") == []


def test_private_finds_held_while_locked_too(state, tmp_path):
    a, *_ = make(state, open_=False)
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    (lib.data_folder / "note.txt").write_text("card 4111 1111 1111 1111 for the trip", encoding="utf-8")
    assert lib.scan()["indexed"] == 1
    assert a.inbox.pending() == 1
    a.unlock("pin", PIN)
    assert a.inbox.pending() == 0 and a.vault.status()["items"] >= 1
