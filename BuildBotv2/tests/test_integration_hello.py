"""End to end through the Core API: add hello-tool from a folder, the Yes, it runs, its tools, its page, stop, remove."""

from conftest import wait_for
from puffpai_core.steward.jobs import pid_alive


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_hello_tool_end_to_end(core, drawbridge, hello_copy, state):
    client = core.app.test_client()
    owner = _bearer(core.tokens.issue_owner_key())
    device = _bearer(core.tokens.issue_device("Sam's laptop"))

    r = client.post("/v1/packages", json={"source": str(hello_copy)}, headers=owner)
    assert r.status_code == 202, r.json
    r0 = r.json
    cid = r.json["consent_id"]
    assert [c["consent_id"] for c in client.get("/v1/consent?status=pending", headers=owner).json] == [cid]

    # Only an owner at a paired device answers a Yes card.
    assert client.post(f"/v1/consent/{cid}/answer", json={"answer": "yes"}, headers=owner).status_code == 403
    pkg_token = core.tokens.issue_package_token("someone-else")
    assert client.post(f"/v1/consent/{cid}/answer", json={"answer": "yes"}, headers=_bearer(pkg_token)).status_code == 403
    sha = r0["card_sha"]
    r = client.post(f"/v1/consent/{cid}/answer", json={"answer": "yes", "factor": "tap", "card_sha": sha},
                    headers=device)
    assert r.status_code == 200 and r.json["status"] == "granted"
    assert core.packages.wait(cid, 300)["status"] == "installed"

    row = client.get("/v1/registry/hello-tool", headers=owner).json
    assert row["status"] == "running" and 18420 <= row["port"] <= 18999
    row = wait_for(lambda: core.caretaker.check("hello-tool")["health"]["ok"] and core.registry.get("hello-tool"), 60, 0.5)
    assert row["health"]["ok"] is True
    assert {a["tool"] for a in row["abilities"]} == {"hello-tool.say_hello", "hello-tool.write_note"}

    # Its page is on the pages address only (D49), with its token added; the owner's key never reaches it.
    assert client.get("/pkg/hello-tool/", headers=owner).status_code == 404
    pages = {"base_url": "http://hello-tool.localhost:8422", "environ_base": {"puffpai.listener": "pages"}}   # D53
    r = client.get("/", **pages)
    assert r.status_code == 200 and r.data == b"hello-tool is running."
    assert client.get("/", headers=owner, **pages).status_code == 403
    assert client.post("/v1/packages/hello-tool/open/main", headers=owner).status_code == 404
    assert "start on port" in client.get("/v1/packages/hello-tool/logs", headers=owner).get_data(as_text=True)

    pids = core.steward.pids("hello-tool")
    assert pids
    r = client.post("/v1/packages/hello-tool/stop", headers=owner)
    assert r.status_code == 200 and r.json["nothing_running"] is True and r.json["row"]["status"] == "stopped"
    assert not any(pid_alive(p) for p in pids)
    assert client.get("/", **pages).status_code == 503

    r = client.post("/v1/packages/hello-tool/start", headers=owner)
    assert r.status_code == 200 and r.json["status"] == "running"
    pids = core.steward.pids("hello-tool") or wait_for(lambda: core.steward.pids("hello-tool"), 10)
    r = client.delete("/v1/packages/hello-tool?keep_data=delete", headers=owner)
    assert r.status_code == 200 and r.json["removed"] and r.json["nothing_running"]
    assert not any(pid_alive(p) for p in pids)
    assert client.get("/v1/registry/hello-tool", headers=owner).status_code == 404
    assert not (state / "packages" / "hello-tool").exists()


def test_add_errors_are_plain(core, hello_copy, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "puffpai_core.zone3.drawbridge", None)      # lane L4's code not here
    client = core.app.test_client()
    owner = _bearer(core.tokens.issue_owner_key())
    assert client.post("/v1/packages", json={}, headers=owner).status_code == 400
    r = client.post("/v1/packages", json={"source": str(hello_copy)}, headers=owner)   # no Drawbridge
    assert r.status_code == 403 and "Drawbridge is not here" in r.json["error"]
    assert client.get("/v1/providers", headers=owner).json[0]["slot"] == "zone1.detector"
