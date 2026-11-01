"""Floor X5, X14, X21: no key bytes in logs, env, Registry, events or any file; no route returns a key.

Archivist is exempt by design: it holds the key in Core memory while the vault
is open or a lease is live (honest limit, 09 section 9). Everything else is checked.
"""

import json
import os

from puffpai_core.api import create_app
from puffpai_core.registry import row_from_manifest
from puffpai_core.zone1 import gate
from z1kit import PIN, canary_key, every_byte_under, make


def test_canary_key_never_leaves_archivist(state, freshtable):
    a, bus, events, _ = make(state)
    key = canary_key()
    a.put_secret("CANARY_KEY", key, unattended=True)             # ticked: a running package may get it (D41)
    handle = a.lend("CANARY_KEY", "toolsmith", "cloud_call", hosts=["api.anthropic.com"], job="job-1")
    assert a.use(handle, borrower="toolsmith", purpose="cloud_call", host="api.anthropic.com") == key.encode()
    env = a.inject("freshtable", ["CANARY_KEY"])                 # Steward's start path returns the env to spawn
    assert set(env) == {"CANARY_KEY"}
    gate.prepare(f"here is my key {key} thanks", True, archivist=a)
    a.lock()
    a.unlock("pin", PIN)
    a.end_job("job-1")

    needle = key.encode()
    assert needle not in every_byte_under(state)                 # logs, keyring, store, index, settings
    assert key not in json.dumps(events)                         # the Bus
    assert key not in json.dumps(list(bus.audit_lines))
    assert key not in json.dumps(a.leases())
    assert key not in repr(a._leases) and key not in str(a._leases)
    assert key not in json.dumps(dict(os.environ))               # inject builds an env; it never sets ours
    log = (state / "logs" / "vault.log").read_text(encoding="utf-8")
    assert "CANARY_KEY" in log and key not in log                # names, never values (F16)


def test_no_route_returns_key_bytes(state, freshtable):
    app = create_app(state)
    core = app.extensions["puffpai"]
    core.registry.add(row_from_manifest(freshtable, "made"))
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Sam's laptop")}
    pkg = {"Authorization": "Bearer " + core.tokens.issue_package_token("freshtable")}
    key = canary_key()
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    assert client.put("/v1/vault/secrets/CANARY_KEY", json={"value": key}, headers=dev).status_code == 201
    seen = []
    for method, url in (("get", "/v1/vault/status"), ("get", "/v1/vault/ways-in"), ("post", "/v1/vault/lock"),
                        ("post", "/v1/vault/unlock"), ("put", "/v1/vault/secrets/CANARY_KEY"),
                        ("delete", "/v1/vault/secrets/OTHER"), ("post", "/v1/vault/hold"),
                        ("post", "/v1/vault/recovery-code/test")):
        r = getattr(client, method)(url, headers=pkg, json={"method": "pin", "secret": PIN, "value": "x"})
        assert r.status_code == 403, (method, url)               # a package token reaches no vault route
        seen.append(r.get_data())
    for method, url in (("get", "/v1/vault/status"), ("get", "/v1/vault/ways-in")):
        seen.append(getattr(client, method)(url, headers=dev).get_data())
    assert all(key.encode() not in body for body in seen)
    assert key.encode() not in (state / "registry.db").read_bytes()
