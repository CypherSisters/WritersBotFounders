"""Integration lane I1: each seam between the lanes, wired to the real code (one seam per thing)."""

import base64
import os

import pytest

from conftest import wait_for, yes
from puffpai_core import seams, wiring
from puffpai_core.packages.manager import AddError

PIN = "Tq7#mz2Lp!"                     # a made-up test PIN (z1kit's)


@pytest.fixture
def core(core):
    """The joins between the lanes, as ``start_core`` (``serve``) makes them; nothing started."""
    from puffpai_core.api.app import wire_core
    wire_core(core)
    return core


# -- Drawbridge (lane L4) on the Package Manager's install path ------------------------------------
def test_the_real_drawbridge_checks_the_install_and_its_verdict_is_on_the_card(core, hello_copy, state):
    assert core.packages.drawbridge is core.zone3.drawbridge                 # the Core's own, with its audit log
    staged = core.packages.add(str(hello_copy))
    shown = staged["card"]["drawbridge"]
    assert shown["verdict"] in ("allow", "warn") and shown["line"] and shown["tree_sha"]
    assert yes(core, staged)["status"] == "installed"
    audit = (state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count('"check_package"') == 2                              # at the card and again at install


def test_the_real_drawbridge_blocks_a_hidden_loader(core, hello_copy):
    blob = base64.b64encode(os.urandom(6000)).decode()
    (hello_copy / "page.html").write_text(f"<html><script>var b='{blob}';document.write(atob(b));</script></html>",
                                          encoding="utf-8")
    with pytest.raises(AddError) as e:
        core.packages.add(str(hello_copy))
    assert e.value.status == 403 and "hides encoded code" in e.value.problems[0] and core.consent.list() == []


def test_the_real_drawbridge_holds_a_tunnel_for_her_card(core, hello_copy):
    (hello_copy / "tunnel.py").write_text("from pyngrok import ngrok\nngrok.connect(8080)\n", encoding="utf-8")
    staged = core.packages.add(str(hello_copy))               # D117: a card, and nothing is added before her Yes
    assert staged["card"]["drawbridge"]["verdict"] == "hold" and staged["card"]["drawbridge"]["held_tunnel"] is True
    assert "can start a tunnel" in " ".join(staged["card"]["drawbridge"]["held_said"]) and core.registry.get(staged["id"]) is None


def test_a_hold_goes_to_her_yes_with_what_is_held_on_the_card(core, hello_copy):
    (hello_copy / "blob.weird").write_bytes(b"\x00\x01\x02 not a kind the checks can read")
    staged = core.packages.add(str(hello_copy))
    shown = staged["card"]["drawbridge"]
    assert shown["verdict"] == "hold" and any("blob.weird" in n for n in shown["notes"])
    assert yes(core, staged)["status"] == "installed"                       # her Yes on that card is the Yes


def test_a_verdict_not_bound_to_the_files_is_a_no(tmp_path, hello_copy):
    from puffpai_core.zone3.drawbridge import Drawbridge
    v = Drawbridge().check_package(hello_copy, {"id": "hello-tool", "kind": "tool"})
    assert seams.drawbridge_bound(hello_copy, v)
    (hello_copy / "server.py").write_text("print('swapped')", encoding="utf-8")
    assert not seams.drawbridge_bound(hello_copy, v)
    assert not seams.drawbridge_bound(hello_copy, {**v, "verdict": "block"})


# -- the one PIN check (lane L3) -----------------------------------------------------------------
def test_consent_and_run_as_admin_share_one_pin_check(core, monkeypatch):
    from puffpai_core.consent import ConsentError
    from puffpai_core.zone1 import archivist
    pin_check = core.app.extensions["puffpai.pin_check"]
    assert pin_check(PIN) == (503, seams.PIN_NOT_SET_UP)                    # no vault yet
    archivist.for_state(core.state, core.bus).setup(PIN)
    calls = []
    real = seams.verify_pin
    monkeypatch.setattr(seams, "verify_pin", lambda *a: (calls.append(a[1]), real(*a))[1])
    assert pin_check(PIN) is None and pin_check("Wrong#pin99") == (403, seams.PIN_WRONG)
    cid = core.consent.request({"kind": "package", "target": "rawreader"}, {"title": "Add it?"}, True)
    sha = core.consent.get(cid)["card_sha"]
    with pytest.raises(ConsentError):
        core.consent.answer(cid, "yes", "pin", "Wrong#pin99", card_sha=sha)
    assert core.consent.answer(cid, "yes", "pin", PIN, card_sha=sha)["status"] == "granted"
    assert calls == [PIN, "Wrong#pin99", "Wrong#pin99", PIN]                # both callers, one function


# -- every package tool call from chat (09 section 3, X27) ------------------------------------
def test_chat_tool_calls_pass_drawbridge_inside_the_install_yes_only(core, drawbridge, hello_copy, state):
    from puffpai_core import wiring
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    review = wiring.tool_review(core, core.zone3.drawbridge, wait_s=0.2)
    q = {"package": "hello-tool", "name": "write_note", "args": {"text": "hi"}, "summary": ["text: hi"]}
    assert review(q)["run"] is True                                           # listed on her install Yes
    got = review({**q, "name": "wipe_everything"})                            # not on the card: asks
    assert got["run"] is False and got["asked"] is True and got["end"] is True
    got = review({**q, "package": "nothing-here", "name": "x"})
    assert got["run"] is False and got["asked"] is False
    assert '"in_scope"' in (state / "logs" / "audit.jsonl").read_text(encoding="utf-8")


# -- Caretaker's listen-check cards go to the Desktop's notices ---------------------------------
def test_caretaker_cards_become_notices(core):
    from puffpai_core.api.app import start_core, stop_core
    core.proxy_port = 0
    terminal = start_core(core)
    try:
        core.caretaker._card("exposed", "hello-tool", "Hello was stopped: it listened on all networks.",
                             ["start_again", "remove"])
        got = core.notices.list()
        assert got and got[0]["title"] == "An app was stopped" and "all networks" in got[0]["text"]
        assert core.zone3.proxy is not None and core.zone3.watch is not None
    finally:
        stop_core(core, terminal)
    assert core.zone3.proxy is None and core.zone3.watch is None


# -- the Bridge proxy credentials (lane L4) in Steward's environment ----------------------------
def test_steward_gets_a_proxy_credential_only_from_the_running_proxy(core):
    env = core.steward.env("hello-tool", {}, 18500, "tok", {})
    assert env["HTTPS_PROXY"] == wiring.NO_PROXY_URL                        # not started: nowhere to go
    port = core.zone3.start_proxy(0)
    try:
        env = core.steward.env("hello-tool", {}, 18500, "tok", {})
        assert env["HTTPS_PROXY"].startswith("http://hello-tool:") and env["HTTPS_PROXY"].endswith(f":{port}")
        assert core.zone3.credentials.check(None) is None
    finally:
        core.zone3.stop()


def test_a_busy_proxy_port_is_a_card_not_a_crash(core, monkeypatch):
    from puffpai_core.api.app import start_core, stop_core

    def busy(port=0):
        raise OSError(10048, "only one usage of each socket address")
    monkeypatch.setattr(core.zone3, "start_proxy", busy)
    terminal = start_core(core)
    try:
        assert core.zone3.proxy is None and core.zone3.cards[-1]["kind"] == "proxy_down"
        assert core.notices.list()[0]["title"] == "The Bridge proxy is not running"
        assert core.steward.env("x", {}, 1, "t", {})["HTTP_PROXY"] == wiring.NO_PROXY_URL
    finally:
        stop_core(core, terminal)


def test_static_packages_serve_their_pictures(core, tmp_path):
    from conftest import REPO
    from puffpai_core.api.app import PAGES_LISTENER, LISTENER
    import shutil
    src = tmp_path / "cloud-avatar"
    shutil.copytree(REPO / "packages" / "cloud-avatar", src)
    assert yes(core, core.packages.add(str(src)))["status"] == "installed"
    client = core.app.test_client()
    env = {LISTENER: PAGES_LISTENER}
    r = client.get("/preview.png", headers={"Host": "cloud-avatar.localhost:8422"}, environ_base=env)
    assert r.status_code == 200 and r.data[:4] == bytes([0x89]) + b"PNG"          # Puff, the owner's drawing
    assert client.get("/README.md", headers={"Host": "cloud-avatar.localhost:8422"},
                      environ_base=env).status_code == 404                  # only what the Desktop loads


def test_the_docker_wall_gets_the_real_proxy_port_or_nothing_starts(core):
    with pytest.raises(RuntimeError):
        core.steward.docker_wall_kw("app")                                  # no proxy: no Docker start
    port = core.zone3.start_proxy(0)
    try:
        assert core.steward.docker_wall_kw("app")["host_proxy_port"] == port   # plain Linux Docker adds its own key
    finally:
        core.zone3.stop()
