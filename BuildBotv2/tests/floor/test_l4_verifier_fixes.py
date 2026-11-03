"""Floor regressions for the L4 security verifier's findings (2026-10-22).

1 policy tamper (X24), 2 ledger at rest (X14), 3 proxy smuggling and schemes (X4),
4 the full Public Suffix List (D28(9)), 5 Drawbridge scans everything (X25),
6 Toolsmith's zone and lane L3's folders (X23), 7 the bypass watch and Steward (D26),
8 one consent store with lane L1's names (X10, D29). No network: names never resolve.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

from conftest import FakeMachine, git_init
from puffpai_core.anchor import sha
from puffpai_core.bus import Bus, Source
from puffpai_core.consent import ConsentError, ConsentStore, card_sha
from puffpai_core.zone3 import hosts, toolsmith_zone
from puffpai_core.zone3 import proxy as proxy_mod
from puffpai_core.zone3.bypass import BypassWatch, find_bypass, tcp_table
from puffpai_core.zone3.chain import GENESIS, ChainLog, line_hash, verify
from puffpai_core.zone3.drawbridge import BLOCK, HOLD, Drawbridge, tree_sha
from puffpai_core.zone3.proxy import Proxy
from puffpai_core.zone3.service import Zone3
from z3helpers import HttpUpstream, grant, manifest, send_all, zone


#: What these forms name exists only on Windows. On Linux and Mac a backslash and a colon are letters of a file name and
#: two slashes are one: tests/test_d132_posix_floor.py holds those cases.
odd_forms = pytest.mark.skipif(os.name != "nt", reason="UNC, device and NTFS stream paths exist only on Windows")


def _drop_link(link):
    """The link itself, never what it points to."""
    os.rmdir(link) if os.name == "nt" else os.unlink(link)


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def _policy_path(state):
    return state / "settings" / "bridge" / "policy.json"


# -- 1. policy tamper -------------------------------------------------------------------------------

def _forge_grant(state, host="evil.com"):
    p = _policy_path(state)
    raw = json.loads(p.read_text(encoding="utf-8"))
    g = dict(raw["body"]["grants"][0], id="g_forged", host=host, subhosts=True)
    raw["body"]["grants"].append(g)
    raw["sha"] = sha(raw["body"])                  # the plain sha, recomputed: the old hole
    time.sleep(0.02)
    p.write_text(json.dumps(raw), encoding="utf-8")


def test_a_forged_grant_with_a_recomputed_sha_is_refused_live_x24(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    _forge_grant(state)
    d = z.bridge.allowed("pkg:freshtable", "evil.com")
    assert not d.allowed and d.reason_code == "bridge_closed"
    assert not z.bridge.allowed("pkg:freshtable", "x.evil.com").allowed
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed     # closed, all of it
    assert z.bridge.posture()["damaged"] and z.bridge.posture()["state"] == "closed"
    assert [c["kind"] for c in z.cards].count("bridge_damaged") == 1, "a card at runtime, once"
    assert any(line["code"] == "policy_damaged" for line in z.bridge.ledger.read())


def test_the_damaged_policy_is_kept_as_evidence_and_opening_restores_the_last_good_copy(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    _forge_grant(state)
    forged = _policy_path(state).read_text(encoding="utf-8")
    z.bridge.close()
    assert _policy_path(state).read_text(encoding="utf-8") == forged, "closing never overwrites the evidence"
    z.bridge.open()
    aside = list((state / "settings" / "bridge").glob("policy.damaged-*.json"))
    assert len(aside) == 1 and aside[0].read_text(encoding="utf-8") == forged
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert not z.bridge.allowed("pkg:freshtable", "evil.com").allowed


def test_a_forged_policy_is_caught_at_start_x24(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    _forge_grant(state)
    z2 = zone(state)
    assert z2.bridge.policy.damaged and "bridge_damaged" in [c["kind"] for c in z2.cards]
    assert not z2.bridge.allowed("pkg:freshtable", "evil.com").allowed


def test_a_stray_edit_with_a_stale_sha_cards_at_runtime(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    raw = json.loads(_policy_path(state).read_text(encoding="utf-8"))
    raw["body"]["grants"].append(dict(raw["body"]["grants"][0], id="g2", host="evil2.com"))
    time.sleep(0.02)
    _policy_path(state).write_text(json.dumps(raw), encoding="utf-8")
    assert not z.bridge.allowed("pkg:freshtable", "evil2.com").allowed
    assert "bridge_damaged" in [c["kind"] for c in z.cards]


def test_no_machine_key_means_closed_with_a_plain_card(state):
    z = Zone3(state, bus=Bus(), machine=FakeMachine(ok=False))
    assert "no_machine_key" in [c["kind"] for c in z.cards]
    assert z.bridge.open()["state"] == "closed"
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    with pytest.raises(ConsentError) as e:                 # a card that can't be sealed is never raised
        z.consents.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    assert e.value.code == "no_machine_key" and e.value.status == 503


def test_a_deleted_machine_key_reads_the_old_policy_as_damaged(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    (state / "settings" / "anchor.key").unlink()
    from puffpai_core import anchor
    anchor._SHARED.clear()                          # a fresh Core start
    z2 = zone(state)
    assert z2.bridge.policy.damaged and not z2.bridge.allowed("pkg:freshtable", "api.example.com").allowed


# -- 2. the ledger at rest -------------------------------------------------------------------------

def _filled(state, n=6):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    for _ in range(n):
        z.bridge.request("pkg:freshtable", "api.example.com")
    return z, z.bridge.ledger.path


def _lines(p):
    return p.read_text(encoding="ascii").splitlines(True)


def _restart_sees(state, why):
    z2 = zone(state)
    notes = z2.bridge.ledger.chain.tampered
    assert notes and notes[0]["why"] == why, notes
    assert "ledger_damaged" in [c["kind"] for c in z2.cards]
    assert z2.bridge.posture()["state"] == "closed"


def test_offline_tail_cut_is_caught_x14(state):
    _z, p = _filled(state)
    L = _lines(p)
    p.write_text("".join(L[:-3]), encoding="ascii", newline="\n")
    assert verify(p) == (True, 0), "the cut file is a whole chain on its own"
    _restart_sees(state, "head_mismatch")


def test_offline_head_cut_is_caught_x14(state):
    _z, p = _filled(state)
    p.write_text("".join(_lines(p)[3:]), encoding="ascii", newline="\n")
    assert verify(p) == (False, 1), "the first line must be n 1 after GENESIS"
    _restart_sees(state, "verify_failed")


def test_a_whole_chain_recomputed_with_plain_hashes_is_caught_x14(state):
    _z, p = _filled(state)
    kept = [json.loads(x) for x in _lines(p)][3:]
    prev, out = GENESIS, []
    for i, line in enumerate(kept, 1):
        line = {k: v for k, v in line.items() if k != "h"}
        line.update(n=i, prev=prev)
        line["h"] = line_hash(line)
        prev = line["h"]
        out.append(json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n")
    p.write_text("".join(out), encoding="ascii", newline="\n")
    assert verify(p) == (True, 0)
    _restart_sees(state, "head_mismatch")


def test_a_deleted_or_forged_seal_is_caught(state):
    _z, p = _filled(state)
    seal = p.with_name(p.name + ".head")
    seal.unlink()
    _restart_sees(state, "no_sealed_head")


def test_a_forged_seal_is_caught(state):
    _z, p = _filled(state)
    L = _lines(p)
    p.write_text("".join(L[:-1]), encoding="ascii", newline="\n")
    last = json.loads(L[-2])
    p.with_name(p.name + ".head").write_text(json.dumps({"n": last["n"], "h": last["h"], "mac": "ab" * 32}),
                                             encoding="ascii")
    _restart_sees(state, "seal_damaged")


def test_a_runtime_edit_with_its_time_put_back_is_caught(state):
    z, p = _filled(state)
    st = p.stat()
    L = _lines(p)
    L[-1] = L[-1].replace('"refused"', '"done____"') if '"refused"' in L[-1] else L[-1].replace('"done"', '"DONE"')
    p.write_text("".join(L), encoding="ascii", newline="\n")
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    z.bridge.request("pkg:freshtable", "api.example.com")
    assert z.bridge.ledger.chain.tampered and "ledger_damaged" in [c["kind"] for c in z.cards]


def test_a_whole_ledger_reopens_clean(state):
    _z, p = _filled(state)
    z2 = zone(state)
    assert not z2.bridge.ledger.chain.tampered and z2.bridge.posture()["state"] == "open"


# -- 3. the proxy -------------------------------------------------------------------------------------

@pytest.fixture
def http_proxy(state, monkeypatch):
    z = zone(state)
    up = HttpUpstream()
    grant(z, manifest(hosts=[f"api.example.com:{up.port}", "api.example.com:443"]))
    px = Proxy(z._proxy_decide, z.bridge.ledger, z.credentials, port=0, resolve=lambda h, p: ["127.0.0.1"])
    port = px.start()
    monkeypatch.setattr(proxy_mod, "answer_refusal", lambda addrs, lan_ok=False: "")
    url = z.package_env("freshtable")["HTTP_PROXY"]
    import base64
    auth = "Basic " + base64.b64encode(url.split("//", 1)[1].rsplit("@", 1)[0].encode()).decode()
    yield z, up, port, auth
    px.stop()
    up.close()


def test_a_pipelined_second_request_is_never_forwarded_x4(http_proxy):
    z, up, port, auth = http_proxy
    first = (f"GET http://api.example.com:{up.port}/one HTTP/1.1\r\nHost: api.example.com\r\n"
             f"Proxy-Authorization: {auth}\r\n\r\n").encode()
    smuggled = b"GET /two HTTP/1.1\r\nHost: evil.com\r\n\r\n"
    r = send_all(port, first + smuggled)
    time.sleep(0.5)
    assert r.startswith(b"HTTP/1.1 200") and b"Connection: close" in r
    assert len(up.got) == 1
    assert b"/two" not in up.got[0] and b"evil.com" not in up.got[0]


def test_a_chunked_body_is_reframed_and_what_follows_is_dropped_x4(http_proxy):
    z, up, port, auth = http_proxy
    req = (f"POST http://api.example.com:{up.port}/in HTTP/1.1\r\nHost: api.example.com\r\n"
           f"Proxy-Authorization: {auth}\r\nTransfer-Encoding: chunked\r\n\r\n").encode()
    body = b"5\r\nhello\r\n0\r\n\r\nGET /two HTTP/1.1\r\nHost: evil.com\r\n\r\n"
    r = send_all(port, req + body)
    time.sleep(0.5)
    assert r.startswith(b"HTTP/1.1 200")
    assert len(up.got) == 1 and up.got[0].endswith(b"5\r\nhello\r\n0\r\n\r\n") and b"evil.com" not in up.got[0]


@pytest.mark.parametrize("bad", [
    "Content-Length: 5\r\nTransfer-Encoding: chunked\r\n",          # both framings
    "Transfer-Encoding: gzip, chunked\r\n",                        # another coding
    "Content-Length: 5\r\nContent-Length: 6\r\n",                  # two lengths
    "X-A: 1\r\n folded: yes\r\n",                                  # an old folded line
    "Bad Name: 1\r\n",                                             # not a header name
])
def test_ambiguous_requests_are_refused_x4(http_proxy, bad):
    z, up, port, auth = http_proxy
    r = send_all(port, (f"POST http://api.example.com:{up.port}/ HTTP/1.1\r\nHost: api.example.com\r\n"
                        f"Proxy-Authorization: {auth}\r\n{bad}\r\nhello").encode())
    assert r.startswith(b"HTTP/1.1 400") and up.got == []


def test_a_bare_line_feed_or_a_user_part_is_refused(http_proxy):
    z, up, port, auth = http_proxy
    r = send_all(port, (f"GET http://api.example.com:{up.port}/ HTTP/1.1\nHost: x\r\n"
                        f"Proxy-Authorization: {auth}\r\n\r\n").encode())
    assert r.startswith(b"HTTP/1.1 400")
    r = send_all(port, (f"GET http://api.example.com:{up.port}@127.0.0.1:{up.port}/ HTTP/1.1\r\n"
                        f"Proxy-Authorization: {auth}\r\n\r\n").encode())
    assert r.startswith(b"HTTP/1.1 400") and up.got == []


def test_a_443_grant_never_allows_plain_http_to_443_x4(http_proxy):
    z, up, port, auth = http_proxy
    r = send_all(port, (f"GET http://api.example.com:443/ HTTP/1.1\r\nHost: api.example.com\r\n"
                        f"Proxy-Authorization: {auth}\r\n\r\n").encode())
    assert r.startswith(b"HTTP/1.1 403") and up.got == []
    assert not z.bridge.request("pkg:freshtable", "api.example.com", port=443, scheme="http").allowed
    assert z.bridge.request("pkg:freshtable", "api.example.com", port=443, scheme="https").allowed
    assert z.bridge.request("pkg:freshtable", "api.example.com", port=up.port, scheme="http").allowed


def test_grant_schemes_follow_the_port(state):
    z = zone(state)
    grant(z, manifest(hosts=["web.example.com:80", "api.example.com"]))
    assert z.bridge.request("pkg:freshtable", "web.example.com", port=80, scheme="http").allowed
    assert not z.bridge.request("pkg:freshtable", "web.example.com", port=80, scheme="https").allowed
    schemes = {g["host"]: g["schemes"] for g in z.bridge.policy.fresh().grants}
    assert schemes == {"web.example.com": ["http"], "api.example.com": ["https"]}


def test_the_proxy_states_what_connect_cannot_see():
    doc = proxy_mod.__doc__
    assert "domain fronting" in doc.lower() and "not stopped" in doc


# -- 4. the full Public Suffix List, offline -------------------------------------------------------

def test_the_full_public_suffix_list_is_bundled_and_read_offline_d28_9():
    from importlib import resources
    data = resources.files("puffpai_core.zone3").joinpath("data", "public_suffix_list.dat").read_text("utf-8")
    assert "// VERSION: 2026-10-20" in data and hosts.psl_loaded()
    for shared in ("co.at", "com.es", "co.th", "uk.com", "eu.org", "r2.dev", "s3.us-east-1.amazonaws.com",
                   "k12.ca.us", "ca.us", "azurestaticapps.net", "foo.ck", "x.kawasaki.jp", "com", "github.io"):
        assert not hosts.subhost_grant_ok(shared), shared
    for own in ("example.com", "api.github.com", "www.ck", "city.kawasaki.jp", "pages.github.com"):
        assert hosts.subhost_grant_ok(own), own


def test_an_unreadable_list_allows_no_sub_host_grant(monkeypatch):
    monkeypatch.setattr(hosts, "_psl", lambda: None)
    assert not hosts.subhost_grant_ok("example.com")


# -- 5. Drawbridge scans and hashes everything ----------------------------------------------------

@pytest.fixture
def db(state):
    return Drawbridge(ChainLog(state / "logs" / "audit.jsonl"))


def _tunnel_files(v) -> list:
    return next((r["tunnel"] for r in v["rows"] if r.get("code") == "cant_check"), [])


@pytest.mark.parametrize("skipdir", ["node_modules", ".venv", "venv", "__pycache__", ".git"])
def test_nothing_is_skipped_at_any_depth_x25(db, tmp_path, skipdir):
    root = tmp_path / "pkg"
    deep = root / "src" / "lib" / skipdir
    deep.mkdir(parents=True)
    (root / "index.js").write_text("console.log('hi')\n", encoding="utf-8")
    (deep / "helper.js").write_text("const ngrok = require('ngrok'); ngrok.connect(8080)\n", encoding="utf-8")
    v = db.check_package(root, manifest("pkgx"), origin="adopted:https://x")
    assert v["verdict"] == HOLD and _tunnel_files(v) == [f"src/lib/{skipdir}/helper.js"]  # found, and her card says so (D117)
    (deep / "helper.js").write_text("module.exports = 1\n", encoding="utf-8")
    before = tree_sha(root)
    (deep / "helper.js").write_text("module.exports = 2\n", encoding="utf-8")
    assert tree_sha(root) != before, "an edit deep inside is in the tree hash"


def test_what_cant_be_checked_asks_never_passes_x25(db, tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "index.js").write_text("console.log('hi')\n", encoding="utf-8")
    (root / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert db.check_package(root, manifest("pkgx"))["verdict"] == "allow"
    (root / "tool.zzz").write_text("whatever\n", encoding="utf-8")            # an unknown kind, plain text:
    assert db.check_package(root, manifest("pkgx"))["verdict"] == "allow"     # read and checked as text (D78)
    (root / "tool.zzz").write_text("require('ngrok'); ngrok.connect(1)\n", encoding="utf-8")
    v = db.check_package(root, manifest("pkgx"))
    assert v["verdict"] == HOLD and _tunnel_files(v) == ["tool.zzz"]          # checked, so caught (D117: her card)
    for binary in (b"whatever\x00\n", b"whatever \xff\xfe\n"):                # a NUL byte; not UTF-8
        (root / "tool.zzz").write_bytes(binary)                               # a kind it can't read
        v = db.check_package(root, manifest("pkgx"))
        assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"]
    (root / "tool.zzz").unlink()
    (root / "fake.png").write_text("require('ngrok')\n", encoding="utf-8")   # not the picture it says,
    v = db.check_package(root, manifest("pkgx"))                              # and read as text (D95): caught
    assert v["verdict"] == HOLD and _tunnel_files(v) == ["fake.png"]
    (root / "fake.png").write_text("hello\n", encoding="utf-8")
    assert db.check_package(root, manifest("pkgx"))["verdict"] == HOLD
    (root / "fake.png").unlink()
    from puffpai_core.zone3 import drawbridge as dmod
    big = root / "data.json"
    big.write_bytes(b"[" + b"0," * (dmod.MAX_TEXT_READ // 2) + b"0]")      # over the read limit
    assert db.check_package(root, manifest("pkgx"))["verdict"] == HOLD
    big.unlink()
    (root / "lib.mts").write_text("import ngrok from 'ngrok'; ngrok.connect(1)\n", encoding="utf-8")
    v = db.check_package(root, manifest("pkgx"))                              # .mts is read as code now
    assert v["verdict"] == HOLD and _tunnel_files(v) == ["lib.mts"]


def test_a_link_inside_the_package_asks(db, tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "index.js").write_text("1\n", encoding="utf-8")
    target = tmp_path / "throwaway_target"         # an empty folder made for this test only
    target.mkdir()
    link = root / "out"
    made = "symlink"
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("this account can't make symlinks")
        made = "junction"                          # a reparse point; needs no admin right
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("this account can't make a junction")
    try:
        v = db.check_package(root, manifest("pkgx"))
        assert v["verdict"] == HOLD and "cant_check" in v["reasons"], made
    finally:
        _drop_link(link)


# -- 6. Toolsmith's zone and lane L3's folders ------------------------------------------------------

@pytest.fixture
def places(tmp_path, monkeypatch):
    st = tmp_path / "state"
    (st / "settings").mkdir(parents=True)
    (st / "packages" / "mytry").mkdir(parents=True)
    linked, data = tmp_path / "MyLinked", tmp_path / "MyData"
    linked.mkdir()
    data.mkdir()
    (linked / "x.txt").write_text("mine", encoding="utf-8")
    (st / "settings" / "folders.json").write_text(json.dumps({"data": str(data), "linked": [str(linked)]}),
                                                  encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_STATE", str(st))
    folder = str(st / "packages" / "mytry")
    return st, folder, linked, data


def _check(st, folder, target, **kw):
    return toolsmith_zone.check(target, folder=folder, zone=toolsmith_zone.zone_for(folder, state=str(st)), **kw)


def test_linked_and_custom_data_folders_are_denied_x23(places):
    st, folder, linked, data = places
    for target in (linked / "x.txt", data / "x.txt", st / "data" / "y.txt"):
        assert _check(st, folder, str(target)).verdict == "deny", target
        assert _check(st, folder, str(target), write=True).verdict == "deny", target
    for cmd in (f'type "{linked / "x.txt"}"', f'cat "{linked / "x.txt"}"', f'more "{data / "a.md"}"',
                f'copy x.txt "{data}"', f'echo hi > "{linked / "n.txt"}"', f'dir "{linked}"'):
        assert _check(st, folder, cmd, kind="command").verdict == "deny", cmd
    assert _check(st, folder, os.path.join(folder, "a.py"), write=True).verdict == "allow"


@pytest.mark.parametrize("text", ["{broken", "[]", '{"data": 5}', '{"linked": "C:/x"}', '{"linked": [""]}'])
def test_an_unreadable_folders_file_refuses_everything_x23(places, text):
    st, folder, _linked, _data = places
    (st / "settings" / "folders.json").write_text(text, encoding="utf-8")
    assert _check(st, folder, os.path.join(folder, "a.py")).verdict == "deny"


def test_python_start_hooks_and_the_cores_python_are_denied_x23(places, tmp_path):
    st, folder, _linked, _data = places
    site = tmp_path / "py" / "Lib" / "site-packages"
    for name in ("evil.pth", "sitecustomize.py", "usercustomize.py"):
        assert _check(st, folder, str(site / name), write=True).verdict == "deny", name
        assert _check(st, folder, str(site / name)).verdict == "deny", name
    assert _check(st, folder, os.path.join(folder, ".venv", "Lib", "site-packages", "x.pth"),
                  write=True).verdict == "deny", "even inside the try's own folder"
    assert _check(st, folder, f'echo import os > "{site / "a.pth"}"', kind="command").verdict == "deny"
    assert _check(st, folder, os.path.join(sys.prefix, "Lib", "site-packages", "x.py"), write=True).verdict == "deny"


# -- 7. the bypass watch and Steward's three calls ------------------------------------------------

class _NoPids:
    def running(self):
        return ["sneaky"]

    def stop(self, package_id, reason):
        pass


def test_a_steward_without_its_calls_is_said_plainly_d26(state):
    z = zone(state)
    watch = z.start_watch(_NoPids())
    s = watch.status()
    assert s["running"] is False and s["reason"] == "steward_incomplete" and s["missing"] == ["pids"]
    assert s["safe_mode"] and "safe mode" in s["line"]
    assert watch.check_once() == [] and watch.status()["running"] is False
    cards = [c for c in z.cards if c["kind"] == "watch_not_running"]
    assert len(cards) == 1 and cards[0]["buttons"] == ["safe_mode"]
    z.stop()


def test_a_failing_steward_is_said_plainly_d26():
    class Broken:
        def running(self):
            raise RuntimeError("down")

        def pids(self, package_id):
            return []

        def stop(self, package_id, reason):
            pass
    cards = []
    watch = BypassWatch(Broken(), on_card=cards.append, table=lambda: [])
    assert watch.check_once() == []
    assert watch.status()["reason"] == "running_failed" and cards and cards[0]["kind"] == "watch_not_running"


class JobSteward:
    """A stand-in for lane L1's Steward with a real Windows Job Object: ``pids`` is every process in it."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateJobObjectW.restype = wintypes.HANDLE
        self.job = self.k.CreateJobObjectW(None, None)
        assert self.job
        self.procs: dict[str, subprocess.Popen] = {}
        self.stopped: list[tuple[str, str]] = []

    def start(self, pkg, argv):
        create_suspended, no_window = 0x4, getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.Popen(argv, creationflags=create_suspended | no_window)
        self.procs[pkg] = p                     # recorded: only this process (and its job) is ever stopped
        h = self.wt.HANDLE(int(p._handle))
        assert self.k.AssignProcessToJobObject(self.wt.HANDLE(self.job), h)
        assert self.ct.WinDLL("ntdll").NtResumeProcess(h) == 0
        return p

    def running(self):
        return [k for k, p in self.procs.items() if p.poll() is None and k not in dict(self.stopped)]

    def pids(self, package_id):
        ct, wt = self.ct, self.wt

        class IdList(ct.Structure):
            _fields_ = [("assigned", wt.DWORD), ("in_list", wt.DWORD), ("ids", ct.c_size_t * 64)]
        buf = IdList()
        assert self.k.QueryInformationJobObject(wt.HANDLE(self.job), 3, ct.byref(buf), ct.sizeof(buf), None)
        return [int(buf.ids[i]) for i in range(buf.in_list)]

    def stop(self, package_id, reason):
        self.stopped.append((package_id, reason))
        self.k.TerminateJobObject(self.wt.HANDLE(self.job), 1)

    def close(self):
        self.k.TerminateJobObject(self.wt.HANDLE(self.job), 1)
        for p in self.procs.values():
            if p.poll() is None:
                p.kill()
            p.wait(10)
        self.k.CloseHandle(self.wt.HANDLE(self.job))


@pytest.mark.skipif(sys.platform != "win32", reason="Job Objects and the TCP table are Windows only")
def test_the_real_program_behind_a_venv_launcher_is_caught_and_stopped_d26(state):
    # sys.executable is this venv's python.exe: a launcher that starts the real Python as its child.
    # The child dials a documentation-only address (192.0.2.1, never routed); nothing is reached.
    steward = JobSteward()
    try:
        parent = steward.start("sneaky", [sys.executable, "-I", "-c",
                                          "import socket,time\ns=socket.socket()\ns.settimeout(15)\n"
                                          "try:\n    s.connect(('192.0.2.1', 9))\nexcept Exception:\n    pass\n"
                                          "time.sleep(15)\n"])
        z = zone(state)
        watch = BypassWatch(steward, ledger=z.bridge.ledger, bus=z.bus, on_card=z.cards.append)
        hits, deadline = [], time.time() + 8
        while not hits and time.time() < deadline:
            hits = find_bypass(steward.pids("sneaky"), tcp_table())
            time.sleep(0.05)
        assert hits, "the direct connection was not seen"
        launcher = getattr(sys, "_base_executable", sys.executable) != sys.executable
        if launcher:
            assert len(steward.pids("sneaky")) >= 2 and hits[0].pid != parent.pid, \
                "the connection belongs to the launcher's child, found through the whole Job Object"
        caught = watch.check_once()
        assert caught and caught[0]["stopped"] is True
        assert steward.stopped == [("sneaky", "Reached the internet around the Bridge")]
        parent.wait(10)
        assert steward.pids("sneaky") == []
        assert any(line["code"] == "around_the_bridge" for line in z.bridge.ledger.read())
    finally:
        steward.close()


# -- 8. one consent store, lane L1's names, the events -------------------------------------------

def _bus_log(bus):
    seen = []
    bus.subscribe("*", lambda m: seen.append(m), "core")
    return seen


def test_lane_l1s_names_work_unchanged_x10(state):
    bus = Bus()
    store = ConsentStore(state / "consent.db", bus)
    card = {"title": "Add FreshTable?", "id": "freshtable", "hosts": ["api.example.com"], "install_hosts": []}
    cid = store.request({"kind": "package", "target": "freshtable"}, card, False)
    assert isinstance(cid, str)
    rec = store.get(cid)
    assert rec["status"] == "pending" and rec["card_sha"] == card_sha(card) and rec["card"] == card
    again = ConsentStore(state / "consent.db", bus)                  # pending cards survive a restart
    assert [r["consent_id"] for r in again.list(status="pending")] == [cid]
    heard = []
    store.on_answer(heard.append)
    timer = threading.Timer(0.2, lambda: store.answer(cid, "yes", "tap", None, by="terminal",
                                                      card_sha=store.get(cid)["card_sha"]))
    timer.start()
    done = store.wait(cid, timeout_s=5)
    assert done["status"] == "granted" and done["by"] == "terminal" and done["factor"] == "tap"
    # wait() wakes when the row changes; answer() tells its listeners after that, on the timer's thread.
    # Wait for that call to finish (the state change we need), so the "no" below can't be heard first.
    timer.join(timeout=10)
    assert not timer.is_alive()
    cid2 = store.request({"kind": "package", "target": "other"}, {"title": "Add Other?"}, False)
    assert store.answer(cid2, "no")["status"] == "declined"
    assert [r["status"] for r in heard] == ["granted", "declined"], "listeners hear yes and no"
    with pytest.raises(ConsentError) as e:
        store.answer(cid, "yes", card_sha=store.get(cid)["card_sha"])
    assert e.value.status == 409


def test_a_package_yes_from_lane_l1_becomes_bridge_grants(state):
    z = zone(state)
    cid = z.consents.request({"kind": "package", "target": "freshtable"},
                             {"title": "Add FreshTable?", "hosts": ["api.example.com"],
                              "install_hosts": ["pypi.org"]}, False)
    z.consents.answer(cid, "yes", "tap", card_sha=z.consents.get(cid)["card_sha"])
    z.bridge.open()
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert z.bridge.request("pkg:freshtable", "pypi.org", phase="install").allowed


def test_the_pin_check_is_on_by_default_through_the_seam_d29(state, monkeypatch):
    from puffpai_core import seams as seams_mod
    from puffpai_core.zone1 import archivist
    store = ConsentStore(state / "consent.db", Bus())
    cid = store.request({"kind": "provider_swap", "target": "vault"}, {"title": "Move your vault?"}, True)
    sha_ = store.get(cid)["card_sha"]
    with monkeypatch.context() as m:                      # lane L3 not here: no Yes, with a plain reason
        m.setattr(seams_mod, "verify_pin", lambda *_a: None)
        with pytest.raises(ConsentError) as e:
            store.answer(cid, "yes", "pin", "2468", card_sha=sha_)
        assert e.value.code == "no_pin_check" and "PIN check is not here yet" in e.value.message
    pin = "Tq7#mz2Lp!"                                    # a made-up test PIN (z1kit's)
    archivist.for_state(state).setup(pin)                 # lane L3's REAL Archivist for this bot folder
    with pytest.raises(ConsentError) as e:
        store.answer(cid, "yes", "pin", "0000", card_sha=sha_)
    assert e.value.code == "step_up_failed"
    assert store.answer(cid, "yes", "pin", pin, card_sha=sha_)["status"] == "granted"


def test_consent_events_match_the_contract_and_are_core_only(state):
    bus = Bus()
    seen = _bus_log(bus)
    store = ConsentStore(state / "consent.db", bus, verify_pin=lambda pin: True)
    bounds = [{"payload_sha": "ab" * 32, "labels": ["person.phone"]},
              {"package": "freshtable", "labels": ["person.phone"]},
              {"plan_sha": "cd" * 32}]
    for kind, bound in zip(("send", "send_always", "device_change"), bounds):
        card = store.ask(kind, "pkg:freshtable" if kind != "device_change" else "plan_1", bound=bound)
        store.answer(card["consent_id"], "yes", "pin", "x", card_sha=card["card_sha"])
        assert store.revoke(card["consent_id"])
    types_seen = [m["type"] for m in seen]
    assert "bus.rejected" not in types_seen, [m["payload"] for m in seen if m["type"] == "bus.rejected"]
    assert types_seen.count("consent.requested") == 3 and types_seen.count("consent.granted") == 3
    assert types_seen.count("consent.revoked") == 3
    granted = [m["payload"] for m in seen if m["type"] == "consent.granted"]
    assert [g["bound"] for g in granted] == bounds
    with pytest.raises(ValueError):
        store.ask("send", "pkg:freshtable", bound={"payload_sha": "ab" * 32, "labels": ["x"], "extra": 1})
    forged = Bus.message("consent.granted", {"consent_id": "c_x", "scope": {"kind": "package", "target": "x"},
                                             "factor": "tap"}, Source("package", "pkg:evil"))
    assert bus.publish(forged) is False


def test_a_revoked_yes_cant_be_turned_back_on_by_an_edit(state):
    import sqlite3
    store = ConsentStore(state / "consent.db", Bus())
    card = store.ask("host", "toolsmith:search", {"hosts": ["api.github.com"]})
    store.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert store.revoke(card["consent_id"])
    with sqlite3.connect(state / "consent.db") as conn:
        conn.execute("UPDATE cards SET status = 'granted'")
    assert store.granted(card["consent_id"]) is None and store.live() == []


def test_pending_cards_through_the_api(state):
    from puffpai_core.api import create_app
    app = create_app(state)
    core = app.extensions["puffpai"]
    c = app.test_client()
    owner = {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}
    from puffpai_core.zone3.service import for_app
    z = for_app(app)
    card = z.bridge.package_card(manifest(hosts=["api.example.com"]))
    pending = c.get("/v1/consent?status=pending", headers=owner).json
    assert [p["consent_id"] for p in pending] == [card["consent_id"]] and pending[0]["status"] == "pending"
    assert c.get("/v1/consent?status=nonsense", headers=owner).status_code == 400


# -- re-check 1: sealed pending cards; the fingerprint covers what the Yes grants (D54) ---------------

def _edit(state, sql):
    import sqlite3
    with sqlite3.connect(state / "consent.db") as conn:
        conn.execute(sql)


def test_a_floor_card_turned_into_a_tap_card_is_void_d54(state):
    z = zone(state)
    card = z.consents.ask("package", "rawreader", {"hosts": []}, floor=True)
    _edit(state, "UPDATE cards SET floor = 0")
    with pytest.raises(ConsentError) as e:
        z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert e.value.code == "void" and "changed outside PuffPAI" in e.value.message
    assert z.consents.get(card["consent_id"])["status"] == "void"
    assert z.consents.list(status="pending") == []


@pytest.mark.parametrize("sql", [
    "UPDATE cards SET covers = replace(covers, 'api.example.com', 'evil.com')",
    "UPDATE cards SET card = replace(card, 'api.example.com', 'evil.com')",
    "UPDATE cards SET card = replace(card, 'api.example.com', 'evil.com'), covers = replace(covers, 'api.example.com', 'evil.com')",
    "UPDATE cards SET scope = replace(scope, 'freshtable', 'evilpkg')",
    "UPDATE cards SET card_sha = 'ab'",
    "UPDATE cards SET seal = NULL",
])
def test_an_edited_host_card_grants_nothing_d54(state, sql):
    z = zone(state)
    card = z.bridge.package_card(manifest(hosts=["api.example.com"]))
    _edit(state, sql)
    with pytest.raises(ConsentError) as e:
        z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert e.value.code == "void"
    z.bridge.open()
    assert z.bridge.posture()["grants"] == []
    assert not z.bridge.allowed("pkg:freshtable", "evil.com").allowed


def test_an_edited_lane_l1_card_is_void_and_l1s_own_check_still_matches(state):
    z = zone(state)
    card = {"title": "Add FreshTable?", "id": "freshtable", "hosts": ["api.example.com"], "install_hosts": []}
    cid = z.consents.request({"kind": "package", "target": "freshtable"}, card, False)
    assert z.consents.get(cid)["card_sha"] == card_sha(card), "L1 hashes its own dict and it matches"
    assert card["puffpai_yes"]["covers"] == {"hosts": ["api.example.com"], "install_hosts": []}
    _edit(state, "UPDATE cards SET covers = replace(covers, 'api.example.com', 'evil.com')")
    with pytest.raises(ConsentError) as e:
        z.consents.answer(cid, "yes", "tap", card_sha=z.consents.get(cid)["card_sha"])
    assert e.value.code == "void"


def test_a_sealed_card_answers_normally(state):
    z = zone(state)
    card = z.bridge.package_card(manifest(hosts=["api.example.com"]))
    assert z.consents.get(card["consent_id"])["status"] == "pending"
    z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    z.bridge.open()
    assert z.bridge.allowed("pkg:freshtable", "api.example.com").allowed


# -- re-check 2: Toolsmith's writes into site-packages, streams, odd paths, joined strings -------------

def _cmd(st, folder, command):
    return _check(st, folder, command, kind="command").verdict


def test_no_write_into_any_site_packages_by_any_copy_x23(places):
    st, folder, _linked, _data = places
    site = os.path.join(folder, ".venv", "Lib", "site-packages")
    os.makedirs(site)
    bs = "\\"                                      # one backslash
    for command in (f'copy a.pth "{site}{bs}"', f'copy a.pth "{site}"', f'move a.pth "{site}"',
                    f'xcopy a.pth "{site}{bs}"', f'Copy-Item a.pth -Destination "{site}"',
                    f'Copy-Item a.pth "{site}"', f'cp a.pth "{site}/"', f'mv a.pth "{site}"',
                    f'robocopy . "{site}" a.pth', f'copy notes.txt "{site}"', f'echo x > "{site}{bs}x.py"'):
        assert _cmd(st, folder, command) == "deny", command
    assert _check(st, folder, os.path.join(site, "plain.py"), write=True).verdict == "deny"
    assert _check(st, folder, os.path.join(site, "plain.py")).verdict == "allow", "reading it is fine"


@odd_forms
def test_ntfs_streams_are_denied_x23(places):
    st, folder, _linked, _data = places
    for target in ("q.pth::$DATA", "a.txt:hidden", "a.txt:hidden:$DATA", os.path.join("sub", "x.py:evil")):
        full = os.path.join(folder, target)
        assert _check(st, folder, full, write=True).verdict == "deny", target
        assert _check(st, folder, full).verdict == "deny", target
    assert _cmd(st, folder, 'copy a.pth "q.pth::$DATA"') == "deny"
    assert _cmd(st, folder, 'echo import os > x.pth:ads') == "deny"


def test_a_colon_that_names_no_stream_stays_fine_x23(places):
    st, folder, _linked, _data = places
    for fine in ("curl localhost:8080", "docker run -p 8080:80 alpine", "python -m http.server 8000"):
        assert _cmd(st, folder, fine) != "deny", fine


@odd_forms
def test_odd_prefixes_deny_writes_and_ask_for_reads_x23(places):
    st, folder, _linked, _data = places
    bs = "\\"
    for target in (bs * 2 + "?" + bs + os.path.join(folder, "a.py"),
                   bs * 2 + "localhost" + bs + "C$" + bs + "Users" + bs + "x" + bs + "a.txt",
                   bs * 2 + "127.0.0.1" + bs + "c$" + bs + "x", "//localhost/C$/x"):
        assert _check(st, folder, target, write=True).verdict == "deny", target
        assert _check(st, folder, target).verdict == "ask", target
    unc = bs * 2 + "localhost" + bs + "C$" + bs + "Users" + bs + "x" + bs
    assert _cmd(st, folder, f'copy a.py "{unc}"') == "deny"


@odd_forms
def test_odd_prefix_read_of_the_tries_own_folder_asks_x23(places):
    st, folder, _linked, _data = places
    bs = "\\"
    for target in (bs * 2 + "?" + bs + os.path.join(folder, "a.py"), bs * 2 + "?" + bs + folder,
                   bs * 2 + "?" + bs + os.path.join(folder, "sub", "..", "a.py"),
                   bs * 2 + "." + bs + os.path.join(folder, "a.py"), "//?/" + os.path.join(folder, "a.py")):
        assert _check(st, folder, target).verdict == "ask", target
        assert _check(st, folder, target, write=True).verdict == "deny", target


@odd_forms
def test_odd_prefix_read_of_any_other_place_stays_denied_x23(places):
    st, folder, _linked, data = places
    bs = "\\"
    packages = str(st / "packages")
    for place in (os.path.join(packages, "other", "a.py"), os.path.join(packages, "mytry-evil", "a.py"),
                  folder + "x" + bs + "a.py", packages, str(st), str(st / "vault"), str(data / "x.txt"),
                  os.path.join(folder, "..", "other", "a.py"), os.path.join(folder, "..")):
        target = bs * 2 + "?" + bs + place
        assert _check(st, folder, target).verdict == "deny", target
        assert _check(st, folder, target, write=True).verdict == "deny", target


@odd_forms
def test_odd_prefix_carve_never_clears_a_place_inside_it_or_a_start_hook_x23(places):
    st, folder, _linked, _data = places
    bs = "\\"
    zone = toolsmith_zone.zone_for(folder, state=str(st))
    inner = os.path.normcase(os.path.join(zone.carve[0], "held"))
    held = toolsmith_zone.Zone(folder=zone.folder, carve=zone.carve, deny=zone.deny + [(inner, toolsmith_zone.DATA)],
                               toolchain=[], home=zone.home)
    for place in (os.path.join(folder, "held", "a.txt"), folder):       # inside it, and the own folder that holds it
        assert held.classify(bs * 2 + "?" + bs + place)[0] == toolsmith_zone.DENY, place
    assert held.classify(bs * 2 + "?" + bs + os.path.join(folder, "a.txt"))[0] == toolsmith_zone.OUTSIDE
    hook = os.path.join(folder, ".venv", "Lib", "site-packages", "a.pth")
    assert zone.classify(hook)[0] == toolsmith_zone.DENY
    assert zone.classify(bs * 2 + "?" + bs + hook)[0] == toolsmith_zone.DENY


@odd_forms
def test_odd_prefix_read_through_a_link_in_the_own_folder_stays_denied_x23(places):
    st, folder, _linked, _data = places
    bs = "\\"
    other = st / "packages" / "other"
    other.mkdir()
    link = os.path.join(folder, "jump")
    try:
        os.symlink(str(other), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if sys.platform != "win32":
            pytest.skip("this account can't make symlinks")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", link, str(other)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("this account can't make a junction")
    try:
        assert _check(st, folder, bs * 2 + "?" + bs + os.path.join(link, "a.py")).verdict == "deny"
    finally:
        os.rmdir(link)                             # removes the link itself, never what it points to


@odd_forms
def test_odd_prefix_never_looks_up_a_share_outside_the_own_folder_x23(places, monkeypatch):
    st, folder, _linked, _data = places
    bs = "\\"
    zone = toolsmith_zone.zone_for(folder, state=str(st))
    asked = []
    real_canon = toolsmith_zone.canon
    monkeypatch.setattr(toolsmith_zone, "canon", lambda p: asked.append(p) or real_canon(p))
    for target in (bs * 2 + "host" + bs + "share" + bs + "a.txt", "//host/share/a.txt",
                   bs * 2 + "?" + bs + "UNC" + bs + "host" + bs + "share" + bs + "a.txt"):
        assert zone.classify(target) == (toolsmith_zone.OUTSIDE, ""), target
    assert asked == []


# An odd path is read as what the kernel would really open. Every case here reads or asks about a real place: the
# junctions are real, and the home folder is a fake one under tmp_path.

LONG, DEVICE, MIXED = "\\\\?\\", "\\\\.\\", "/\\?\\"
BS = "\\"


@pytest.fixture
def junctions():
    """Makes real junctions, and removes each with ``cmd /c rmdir`` before tmp_path goes away."""
    winapi = pytest.importorskip("_winapi", reason="junctions are a Windows thing")
    made = []

    def make(target, link):
        winapi.CreateJunction(str(target), LONG + str(link))       # the long form can name a trailing dot or space
        made.append(LONG + str(link))
        return str(link)
    yield make
    for link in made:
        subprocess.run(f'cmd /c rmdir "{link}"', capture_output=True)   # always quoted: cmd splits on a no-break space
        assert not os.path.lexists(link), link


def _other_app(st):
    other = st / "packages" / "other"
    other.mkdir()
    (other / "a.py").write_text("x", encoding="utf-8")
    return other


def _refused_both_ways(st, folder, target):
    assert _check(st, folder, target).verdict == "deny", target
    assert _check(st, folder, target, write=True).verdict == "deny", target


def _admin_share(place, host="localhost"):
    r"""*place* as ``\\host\C$\...``."""
    drive, tail = os.path.splitdrive(str(place))
    return BS * 2 + host + BS + drive[0] + "$" + tail


def test_odd_path_link_with_a_trailing_dot_or_space_is_denied_x23(places, junctions):
    st, folder, _linked, _data = places
    other = _other_app(st)
    for name in ("dotjump.", "spjump "):
        link = junctions(other, os.path.join(folder, name))
        assert os.path.exists(LONG + os.path.join(link, "a.py")), "the kernel really follows this name"
        for target in (LONG + os.path.join(link, "a.py"), LONG + link, LONG + link + BS, "//?/" + link + "/a.py"):
            _refused_both_ways(st, folder, target)
    for command in (f'type "{LONG}{folder}{BS}spjump {BS}a.py"', f'type "{LONG}{folder}{BS}spjump "',
                    f'type "{LONG}{folder}{BS}dotjump.{BS}a.py"'):
        assert _cmd(st, folder, command) == "deny", command


def test_a_command_word_keeps_a_no_break_space_in_a_name_x23(places, junctions):
    st, folder, _linked, _data = places
    link = junctions(_other_app(st), os.path.join(folder, "jump\u00a0"))
    assert _cmd(st, folder, f'dir "{link}"') == "deny"
    assert _cmd(st, folder, f'dir "{LONG}{link}"') == "deny"


def test_odd_path_link_a_normal_path_would_strip_down_to_is_denied_x23(places, junctions):
    r"""``\\.\`` takes the trailing dot off before the kernel looks, so ``jump.`` opens the link ``jump``."""
    st, folder, _linked, _data = places
    junctions(_other_app(st), os.path.join(folder, "jump"))
    for tail in ("jump.", "jump ", "jump. ."):
        _refused_both_ways(st, folder, DEVICE + os.path.join(folder, tail))


@odd_forms
def test_odd_path_trailing_dot_only_passes_on_a_last_name_in_the_own_folder_x23(places, tmp_path):
    st, folder, _linked, _data = places
    for tail in ("a.py.", "a.py ", "a.py. ."):
        for prefix in (LONG, DEVICE):
            target = prefix + os.path.join(folder, tail)
            assert _check(st, folder, target).verdict == "ask", target
            assert _check(st, folder, target, write=True).verdict == "deny", target
    for target in (LONG + os.path.join(folder, "sub.", "a.py"), LONG + os.path.join(folder, "sub ", "a.py"),
                   LONG + str(tmp_path / "elsewhere" / "a.py."), BS * 2 + "host" + BS + "share" + BS + "a.txt.",
                   BS * 2 + "host" + BS + "share" + BS + "sub " + BS + "a.txt"):
        _refused_both_ways(st, folder, target)


@odd_forms
@pytest.mark.parametrize("name", ["a.pth.", "a.pth..", "a.pth. .", "a.pth ", "sitecustomize.py.", "usercustomize.py "])
def test_odd_path_start_hook_with_a_trailing_dot_or_space_is_denied_x23(places, tmp_path, name):
    st, folder, _linked, _data = places
    site = os.path.join(folder, ".venv", "Lib", "site-packages")
    os.makedirs(site)
    for target in (DEVICE + os.path.join(site, name), LONG + os.path.join(site, name),
                   "//./" + os.path.join(site, name), LONG + str(tmp_path / "py" / "site-packages" / name)):
        assert toolsmith_zone.zone_for(folder, state=str(st)).classify(target) == (toolsmith_zone.DENY,
                                                                                   toolsmith_zone.PYSTART), target
        _refused_both_ways(st, folder, target)


def test_start_hook_and_site_packages_through_a_link_are_denied_x23(places, junctions):
    st, folder, _linked, _data = places
    site = os.path.join(folder, ".venv", "Lib", "site-packages")
    os.makedirs(site)
    link = junctions(site, os.path.join(folder, "sitejump"))
    for prefix in (LONG, DEVICE, "/\\?\\", ""):
        _refused_both_ways(st, folder, prefix + os.path.join(link, "a.pth"))
    assert _check(st, folder, os.path.join(link, "plain.py"), write=True).verdict == "deny"
    assert _check(st, folder, LONG + os.path.join(link, "plain.py")).verdict == "ask", "reading it is fine"
    # A link NAMED site-packages that leads somewhere plain: the path's own words count too.
    away = os.path.join(folder, "plain")
    os.makedirs(away)
    os.makedirs(os.path.join(folder, "v2", "Lib"))
    named = junctions(away, os.path.join(folder, "v2", "Lib", "site-packages"))
    for prefix in (LONG, ""):
        _refused_both_ways(st, folder, prefix + os.path.join(named, "a.pth"))
    assert _check(st, folder, os.path.join(named, "plain.py"), write=True).verdict == "deny"


@odd_forms
def test_mixed_slash_starts_are_the_same_odd_form_x23(places, tmp_path, monkeypatch):
    st, folder, _linked, _data = places
    other = _other_app(st)
    home = tmp_path / "fakehome"
    (home / ".ssh").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    place = str(other / "a.py")
    fwd = place.replace(BS, "/")
    drive, tail = os.path.splitdrive(fwd)
    for target in (MIXED + place, "\\/?/" + fwd, "/\\./" + fwd, "\\/./" + fwd, "/\\localhost/" + drive[0] + "$" + tail,
                   "\\/localhost/" + drive[0] + "$" + tail, "/\\?\\UNC\\localhost\\" + drive[0] + "$" + tail,
                   "/\\?\\" + fwd.replace(":/", "://")):
        _refused_both_ways(st, folder, target)
    keys = str(home / ".ssh" / "authorized_keys")
    for target in (place, keys):
        fw = target.replace(BS, "/")
        for command in (f'echo hi > "{MIXED}{target}"', f'echo hi >> "\\/?/{fw}"', f'echo hi > "//?/{fw}"',
                        f'echo hi > "//?/{fw.replace(":/", "://")}"', f'echo hi > "/\\?\\{fw.replace(":/", "://")}"',
                        f'copy a.py "{MIXED}{target}"', f'Set-Content -Path "{MIXED}{target}" -Value x',
                        f'Out-File -FilePath:{MIXED}{target}', f'type "{MIXED}{target}"', f'cat "\\/?/{fw}"',
                        f'type "{LONG}{target}"'):
            assert _cmd(st, folder, command) == "deny", command
    fine = LONG + os.path.join(folder, "a.py")
    for command in (f'type "{fine}"', f'type "{MIXED}{folder}{BS}a.py"', f'dir "{LONG}{folder}{BS}*.py"',
                    f'type {BS * 2}host{BS}share{BS}a.txt'):
        assert _cmd(st, folder, command) == "ask", command
    for command in (f'echo hi > "{fine}"', f'echo hi > "{MIXED}{folder}{BS}a.py"', f'copy a.py "{fine}"'):
        assert _cmd(st, folder, command) == "deny", command


@odd_forms
def test_mixed_slash_start_of_her_keys_denies_like_the_plain_path_x23(places, tmp_path, monkeypatch):
    st, folder, _linked, _data = places
    home = tmp_path / "fakehome"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("a made-up test key", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    key = str(home / ".ssh" / "id_rsa")
    assert toolsmith_zone.zone_for(folder, state=str(st)).classify(key)[1] == toolsmith_zone.KEYS
    for target in (key, MIXED + key, "\\/?/" + key.replace(BS, "/"), LONG + key, DEVICE + key, "//?/" + key,
                   _admin_share(key), "/\\?\\UNC\\" + _admin_share(key)[2:]):
        assert _check(st, folder, target).verdict == "deny", target
        assert toolsmith_zone.zone_for(folder, state=str(st)).classify(target)[0] == toolsmith_zone.DENY, target


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST", "localhost.", "127.0.0.1", "127.1", "127.255.0.1", "0x7f.0.0.1"])
def test_loopback_admin_share_reads_as_the_local_path_x23(places, junctions, tmp_path, host):
    st, folder, _linked, _data = places
    other = _other_app(st)
    assert _check(st, folder, _admin_share(other / "a.py", host)).verdict == "deny"
    # Links are followed on this form too: it is this computer's own disk, no host the model named.
    outside = tmp_path / "outside"
    outside.mkdir()
    jump = junctions(st, outside / "statejump")
    assert _check(st, folder, _admin_share(os.path.join(jump, "vault", "x"), host)).verdict == "deny"
    plain = tmp_path / "outside" / "a.txt"
    assert _check(st, folder, _admin_share(plain, host)).verdict == "ask"
    assert _check(st, folder, _admin_share(plain, host), write=True).verdict == "deny"
    assert _check(st, folder, _admin_share(os.path.join(folder, "a.py"), host)).verdict == "ask"


@odd_forms
def test_long_unc_and_this_computers_own_name_read_as_the_local_path_x23(places, monkeypatch):
    st, folder, _linked, _data = places
    other = _other_app(st)
    monkeypatch.setenv("COMPUTERNAME", "ThisBox")
    share = _admin_share(other / "a.py")[2:]
    for target in (LONG + "UNC" + BS + share, DEVICE + "UNC" + BS + share, "\\??\\UNC\\" + share,
                   _admin_share(other / "a.py", "thisbox"), LONG + "unc" + BS + _admin_share(other / "a.py", "THISBOX")[2:]):
        _refused_both_ways(st, folder, target)
    assert _check(st, folder, LONG + "UNC" + BS + _admin_share(os.path.join(folder, "a.py"))[2:]).verdict == "ask"


@odd_forms
@pytest.mark.parametrize("target", [
    "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\x", "\\\\.\\Volume{00000000-0000-0000-0000-000000000000}\\x",
    "/\\?\\Volume{00000000-0000-0000-0000-000000000000}\\x", "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume3\\x",
    "\\\\.\\GLOBALROOT\\Device\\HarddiskVolume3\\x", "//?/GLOBALROOT/Device/HarddiskVolume3/x",
    "\\Device\\HarddiskVolume3\\x", "\\\\.\\PhysicalDrive0", "\\\\.\\pipe\\x", "\\\\?\\HarddiskVolume3\\x",
    "\\\\?\\BootPartition\\x", "\\\\?\\", "\\\\.\\", "\\\\?", "\\\\?\\UNC", "\\\\?\\UNC\\", "\\\\\\foo\\bar",
    "\\\\\\\\?\\C:\\x", "\\\\?\\C:x", "\\??\\C:\\x", "\\\\?\\\\\\?\\C:\\x", "\\\\?\\UNC\\?\\C:\\x", "\\\\.\\C:",
    "\\\\localhost\\ADMIN$\\x", "\\\\localhost\\IPC$", "\\\\localhost\\Users\\x\\a.txt", "\\\\localhost\\C$.\\x",
    "\\\\localhost\\.\\C$\\x", "\\\\127.0.0.1\\share\\x", "\\\\nas-nobody\\C$\\x", "\\\\192.168.1.5\\c$\\x",
    "\\\\192.168.1.5\\ADMIN$\\x", "\\\\192.168.1.5\\C$ \\x", "\\\\192.168.1.5\\.\\C$\\x", "//192.168.1.5/c$/x",
])
def test_odd_forms_that_cannot_be_mapped_are_denied_x23(places, target):
    st, folder, _linked, _data = places
    _refused_both_ways(st, folder, target)


def test_a_link_outside_the_try_into_the_state_folder_is_denied_x23(places, junctions, tmp_path):
    st, folder, _linked, _data = places
    other = _other_app(st)
    outside = tmp_path / "outside"
    outside.mkdir()
    jump = junctions(st, outside / "statejump")
    place = os.path.join(jump, "packages", "other", "a.py")
    assert os.path.exists(place), "the kernel really follows it"
    for target in (place, LONG + place, DEVICE + place, MIXED + place, _admin_share(place)):
        _refused_both_ways(st, folder, target)
    assert other.is_dir()


def test_short_names_are_expanded_before_the_check_x23(places, tmp_path):
    st, folder, _linked, _data = places
    ctypes = pytest.importorskip("ctypes")
    if os.name != "nt":
        pytest.skip("8.3 names are a Windows thing")
    (st / "packages" / "a long other app name").mkdir()
    other = st / "packages" / "a long other app name"
    (other / "a.py").write_text("x", encoding="utf-8")
    buf = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(other), buf, 32768) or buf.value.lower() == str(other).lower():
        pytest.skip("this volume makes no 8.3 names")
    short = buf.value
    assert "~" in short
    for place in (os.path.join(short, "a.py"), os.path.join(short, "not there yet.py"), short):
        for target in (place, LONG + place, DEVICE + place, MIXED + place, _admin_share(place)):
            assert _check(st, folder, target).verdict == "deny", target
    (tmp_path / "a long start hook folder name" / "site-packages").mkdir(parents=True)
    hooks = tmp_path / "a long start hook folder name" / "site-packages" / "a long hook file name.pth"
    hooks.write_text("x", encoding="utf-8")
    if ctypes.windll.kernel32.GetShortPathNameW(str(hooks), buf, 32768) and buf.value.lower() != str(hooks).lower():
        assert toolsmith_zone.zone_for(folder, state=str(st)).classify(LONG + buf.value)[1] == toolsmith_zone.PYSTART


@odd_forms
def test_odd_forms_never_look_up_a_host_the_model_named_x23(places, monkeypatch):
    st, folder, _linked, _data = places
    host = BS * 2 + "nas-nobody" + BS + "share"
    asked = []

    def spy(real):
        def look(p, *a, **kw):
            asked.append(str(p))
            return False if "nas-nobody" in str(p) else real(p, *a, **kw)
        return look
    monkeypatch.setattr(toolsmith_zone, "canon", spy(toolsmith_zone.canon))
    monkeypatch.setattr(os.path, "isdir", spy(os.path.isdir))
    monkeypatch.setattr(os.path, "realpath", spy(os.path.realpath))
    for command, want in ((f'type {host}{BS}a.txt', "ask"), (f'copy a.py {host}', "deny"), (f'copy a.py {host}{BS}', "deny"),
                          (f'docker run -v {host}:/x alpine', "ask"), (f'pip install --target {host}{BS}t foo', "deny"),
                          (f'cd {host}', "ask"), (f'python -m pip install --target={host} foo', "deny")):
        assert _cmd(st, folder, command) == want, command
    assert toolsmith_zone.check(f"services:\n  a:\n    volumes:\n      - //nas-nobody/share/x:/x\n", folder=folder,
                                kind="compose", cwd=folder,
                                zone=toolsmith_zone.zone_for(folder, state=str(st))).verdict == "allow"
    assert not [p for p in asked if "nas-nobody" in p]


def test_normal_paths_stay_as_they_were_x23(places, junctions, tmp_path):
    st, folder, _linked, _data = places
    for name in ("caf\u00e9 \u00fcber", "with space", "name.with.dots", "trailing"):
        os.makedirs(os.path.join(folder, name))
        for path in (os.path.join(folder, name, "a b.py"), os.path.join(folder, name) + BS, os.path.join(folder, name)):
            assert _check(st, folder, path).verdict == "allow", path
            assert _check(st, folder, path, write=True).verdict == "allow", path
    assert _check(st, folder, f'type "{folder}{BS}caf\u00e9 \u00fcber{BS}a b.py"', kind="command").verdict == "allow"
    assert _check(st, folder, "C:\\Program Files\\Some App\\a.txt").verdict != "deny"
    assert _check(st, folder, "C:\\Program Files\\Some App\\", write=True).verdict != "deny"
    away = tmp_path / "a folder of hers"
    away.mkdir()
    assert _check(st, folder, str(away) + BS).verdict == "ask"
    # A trailing dot on a plain path: Windows takes it off, and so does the check.
    assert _check(st, folder, os.path.join(folder, "a.py.")).verdict == "allow"
    # The own folder by an odd path, with a link that stays inside it: still a question, never an outright allow.
    inner = os.path.join(folder, "with space")
    link = junctions(inner, os.path.join(folder, "innerjump"))
    assert _check(st, folder, os.path.join(link, "a.py")).verdict == "allow"
    assert _check(st, folder, LONG + os.path.join(link, "a.py")).verdict == "ask"
    assert _check(st, folder, LONG + os.path.join(folder, "with space", "a b.py")).verdict == "ask"
    # A real share, with spaces and a trailing slash, waits for her Yes for a read and is denied for a write.
    for target in (BS * 2 + "server" + BS + "share", BS * 2 + "server" + BS + "share" + BS,
                   BS * 2 + "server" + BS + "my share" + BS + "a b" + BS + "c.txt", "//server/share/caf\u00e9.txt",
                   "/\\server/share/a.txt", LONG + "UNC" + BS + "server" + BS + "share" + BS + "a.txt",
                   BS * 2 + "nas" + BS + "backup$" + BS + "a.txt"):
        assert _check(st, folder, target).verdict == "ask", target
        assert _check(st, folder, target, write=True).verdict == "deny", target


def test_joined_strings_and_unresolved_targets_x23(places):
    st, folder, _linked, _data = places
    bs = "\\"
    for command in (f'Copy-Item a.pth ("C:{bs}x{bs}site-" + "packages")', '$d = "site-" + "packages"; cp a.pth $d',
                    'Copy-Item a.pth (Join-Path $env:TEMP "x")', 'copy a.pth $dest', 'echo x > %OUTFILE%',
                    'Set-Content -Path ("a" + ".pth") -Value x'):
        assert _cmd(st, folder, command) == "deny", command
    assert _cmd(st, folder, f'Get-Content ("C:{bs}x{bs}" + "y.txt")') == "ask"


# -- re-check 3: D54, the vault keeps the Bridge's version, log positions and consent generation ------
# These run against lane L3's REAL Archivist (zone1/archivist.py, on main since 81f4481c): a set-up vault
# with a made-up test PIN and a fake machine key (z1kit), locked and unlocked for real.

from puffpai_core.zone1.archivist import Archivist as RealArchivist  # noqa: E402
from z1kit import PIN, make as make_archivist  # noqa: E402


class Arch:
    """The real Archivist plus two test helpers (lock, unlock with the test PIN)."""

    def __init__(self, state):
        self.a, _bus, _events, _code = make_archivist(state)

    def lock(self):
        self.a.lock()

    def unlock(self):
        self.a.unlock("pin", PIN)                         # runs every on_unlock callback, as in the Core

    def kept(self, name):
        return self.a.anchor_get(name)


def test_the_bridge_uses_lane_l3s_real_archivist_contract():
    from puffpai_core.zone3.vault_anchor import ARCHIVIST_CONTRACT, contract_problems
    assert contract_problems(RealArchivist) == [], "lane L3's Archivist drifted from what the Bridge calls"
    assert set(ARCHIVIST_CONTRACT) == {"is_open", "anchor_put", "anchor_get", "on_unlock"}
    from puffpai_core.zone1 import archivist as real
    assert callable(real.for_state)


def _z(state, arch):
    return Zone3(state, bus=Bus(), archivist=arch.a)


def test_changes_go_to_the_vault_and_wait_while_locked_d54(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    assert arch.kept("bridge.policy") == {"version": z.bridge.policy.version, "sha": z.bridge.policy.sha()}
    z.bridge.request("pkg:freshtable", "api.example.com")
    head, n = z.bridge.ledger.chain.head()
    assert arch.kept("bridge.ledger") == {"n": n, "h": head}
    arch.lock()
    z.bridge.close()
    z.bridge.request("pkg:freshtable", "api.example.com")
    assert {"bridge.policy", "bridge.ledger"} <= set(z.vault.pending), \
        "anchor_put returned False while locked: kept pending, never silently skipped"
    arch.unlock()
    assert z.vault.pending == {} and arch.kept("bridge.policy")["version"] == z.bridge.policy.version
    assert arch.kept("bridge.ledger")["n"] == z.bridge.ledger.chain.head()[1]
    assert "vault_mismatch" not in [c["kind"] for c in z.cards]


def test_a_rolled_back_policy_is_caught_at_unlock_d54(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    d = state / "settings" / "bridge"
    old = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file()}   # an old, genuinely sealed copy
    grant(z, manifest("other", hosts=["api.other.org"]))
    z.bridge.close()
    arch.lock()
    z.stop()                                                              # the old Core stops
    for name, data in old.items():                                       # a same-user program puts it back
        (d / name).write_bytes(data)
    z2 = _z(state, arch)                                                  # passes the machine-key seals
    assert not z2.bridge.policy.damaged and z2.bridge.posture()["state"] == "open"
    arch.unlock()
    assert z2.bridge.posture()["state"] == "closed"
    card = [c for c in z2.cards if c["kind"] == "vault_mismatch"]
    assert card and "bridge.policy" in card[0]["what"] and "vault remembers" in card[0]["line"]


def test_a_deleted_log_and_its_seal_are_caught_at_unlock_d54(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    for _ in range(3):
        z.bridge.request("pkg:freshtable", "api.example.com")
    arch.lock()
    z.stop()
    for p in (state / "logs").glob("outside.jsonl*"):
        p.unlink()                                                        # the log AND its sealed head
    z2 = _z(state, arch)
    assert not z2.bridge.ledger.chain.tampered, "a fresh log looks fine on its own"
    arch.unlock()
    assert z2.bridge.posture()["state"] == "closed"
    assert "bridge.ledger" in [c for c in z2.cards if c["kind"] == "vault_mismatch"][0]["what"]


def test_an_anchor_that_cant_be_read_at_unlock_closes_the_bridge_d54(state, monkeypatch):
    from puffpai_core.zone1.vault import VaultError
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    real_get = arch.a.anchor_get

    def tampered(name):
        if name == "bridge.policy":
            raise VaultError("That anchor can't be read.")        # what L3 raises for a changed blob
        return real_get(name)
    monkeypatch.setattr(arch.a, "anchor_get", tampered)
    arch.lock()
    arch.unlock()
    assert z.bridge.posture()["state"] == "closed"
    card = [c for c in z.cards if c["kind"] == "vault_mismatch"][0]
    assert card["why"] == ["cant_confirm"] and "couldn't confirm" in card["line"]


def test_a_clean_restart_and_unlock_keeps_the_bridge_open_d54(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    z.bridge.request("pkg:freshtable", "api.example.com")
    arch.lock()
    z.stop()
    z2 = _z(state, arch)
    z2.bridge.request("pkg:freshtable", "api.example.com")               # a line written while locked
    arch.unlock()
    assert z2.bridge.posture()["state"] == "open" and "vault_mismatch" not in [c["kind"] for c in z2.cards]


def test_the_policy_version_only_goes_up_d54(state):
    z = zone(state)
    grant(z, manifest(hosts=["api.example.com"]))
    high = z.bridge.policy.version
    assert high >= 2
    _policy_path(state).unlink()                                          # deleted on a bot that has run
    z2 = zone(state)
    assert z2.bridge.policy.damaged and "bridge_damaged" in [c["kind"] for c in z2.cards]
    z2.bridge.open()
    assert z2.bridge.policy.version == high + 1, "a new start never reuses an old number"
    hw = state / "settings" / "bridge" / "policy.version"
    hw.write_text(json.dumps({"version": 1, "mac": "ab" * 32}), encoding="utf-8")
    assert zone(state).bridge.policy.damaged, "an edited version mark reads damaged"


# -- re-check 4: consent replay ------------------------------------------------------------------

def _rows(state):
    import sqlite3
    with sqlite3.connect(state / "consent.db") as conn:
        return {r[0]: r for r in conn.execute("SELECT id, status, sha, mac FROM cards")}


def test_a_revoked_yes_put_back_row_by_row_stays_dead(state):
    import sqlite3
    z = zone(state)
    keep = grant(z, manifest("keep", hosts=["api.keep.org"]))
    gone = grant(z, manifest(hosts=["api.example.com"]))
    before = _rows(state)[gone["consent_id"]]
    assert z.consents.revoke(gone["consent_id"])
    with sqlite3.connect(state / "consent.db") as conn:                 # old status, sha and MAC put back
        conn.execute("UPDATE cards SET status = ?, sha = ?, mac = ? WHERE id = ?",
                     (before[1], before[2], before[3], gone["consent_id"]))
    assert z.consents.granted(gone["consent_id"]) is None
    assert not z.bridge.allowed("pkg:freshtable", "api.example.com").allowed
    assert z.consents.granted(keep["consent_id"]), "the Yes still live was sealed again"
    assert z.bridge.allowed("pkg:keep", "api.keep.org").allowed


def test_a_deleted_generation_reads_damaged_not_one(state):
    import sqlite3
    z = zone(state)
    rec = grant(z, manifest(hosts=["api.example.com"]))
    with sqlite3.connect(state / "consent.db") as conn:
        conn.execute("DELETE FROM meta")
    assert z.consents.generation() is None and z.consents.granted(rec["consent_id"]) is None


def test_a_whole_old_consent_file_is_caught_at_unlock_d54(state):
    import shutil
    arch = Arch(state)
    z = _z(state, arch)
    rec = grant(z, manifest(hosts=["api.example.com"]))
    shutil.copy(state / "consent.db", state / "consent.old")              # an old copy, genuinely sealed
    assert z.consents.revoke(rec["consent_id"])
    assert arch.kept("consent.generation") == {"generation": 2}
    arch.lock()
    z.stop()
    shutil.copy(state / "consent.old", state / "consent.db")
    z2 = _z(state, arch)
    assert z2.consents.granted(rec["consent_id"]), "on its own the old file passes"
    arch.unlock()
    assert z2.bridge.posture()["state"] == "closed"
    assert "consent.generation" in [c for c in z2.cards if c["kind"] == "vault_mismatch"][0]["what"]


def test_a_yes_without_its_card_sha_is_refused(state):
    z = zone(state)
    card = z.bridge.package_card(manifest(hosts=["api.example.com"]))
    with pytest.raises(ConsentError) as e:
        z.consents.answer(card["consent_id"], "yes", "tap")
    assert e.value.code == "no_card_sha" and e.value.status == 400
    assert z.consents.answer(card["consent_id"], "no")["status"] == "declined", "a No only narrows"


# -- re-check 5: the post-command scan of Python start-up hooks ----------------------------------------

import shutil  # noqa: E402
import zipfile  # noqa: E402

from puffpai_core.zone3.hook_guard import HookGuard  # noqa: E402

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(argv, cwd):
    """Run one real command to its end (it exits by itself; nothing is left running)."""
    return subprocess.run(argv, cwd=cwd, capture_output=True, timeout=120, creationflags=_NO_WINDOW)


def _ps(command, cwd):
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell is not here")
    return _run([exe, "-NoProfile", "-NonInteractive", "-Command", command], cwd)


@pytest.fixture
def hooks(places, tmp_path):
    st, folder, _linked, _data = places
    site_dir = os.path.join(folder, ".venv", "Lib", "site-packages")
    os.makedirs(site_dir)
    with open(os.path.join(site_dir, "kept.pth"), "w", encoding="utf-8") as fh:
        fh.write("./kept\n")                             # a hook that was there before: must survive
    evil = os.path.join(folder, "evil")
    os.makedirs(os.path.join(evil, "site-packages"))
    for p in (os.path.join(folder, "evil.pth"), os.path.join(evil, "site-packages", "evil.pth")):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("import os; os.system('calc')\n")    # never run: Python never starts on it here
    core = tmp_path / "fakecore" / "site-packages"       # a stand-in for the Core's own site folder
    core.mkdir(parents=True)
    stopped, safe, cards = [], [], []
    guard = lambda: HookGuard(folder, core_sites=[str(core)], stop_try=stopped.append,  # noqa: E731
                              safe_mode=safe.append, on_card=cards.append)
    return st, folder, site_dir, guard, stopped, safe, cards, core


def _caught(g, site_dir, stopped, cards):
    assert g.result["action"] == "stopped_try" and not g.result["ok"], g.result
    assert stopped == ["python_hook_changed"] and cards[-1]["kind"] == "try_hook_changed"
    left = [os.path.join(d, f) for d, _s, fs in os.walk(site_dir) for f in fs if f.endswith(".pth")]
    assert [os.path.basename(x) for x in left] == ["kept.pth"], "the change was undone, the old hook kept"


def test_scan_catches_copy_item_destination_colon(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    cmd = "Copy-Item evil.pth -Destination:.venv\\Lib\\site-packages\\"
    assert _cmd(st, folder, cmd) == "deny", "the text check refuses this one too"
    with guard() as g:
        assert _ps(cmd, folder).returncode == 0
    _caught(g, site_dir, stopped, cards)


def test_scan_catches_cp_r_dot_into_lib(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    cp = shutil.which("cp")
    if not cp:
        pytest.skip("no cp here")
    assert _cmd(st, folder, "cp -r evil/. .venv/Lib/") != "deny", "the text check can't see where it lands"
    with guard() as g:
        assert _run([cp, "-r", "evil/.", ".venv/Lib/"], folder).returncode == 0
    _caught(g, site_dir, stopped, cards)


def test_scan_catches_copy_item_recurse(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    cmd = "Copy-Item -Recurse -Force evil\\site-packages .venv\\Lib\\"
    with guard() as g:
        assert _ps(cmd, folder).returncode == 0
    _caught(g, site_dir, stopped, cards)


def test_scan_catches_expand_archive(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    with zipfile.ZipFile(os.path.join(folder, "evil.zip"), "w") as zf:
        zf.writestr("evil.pth", "import os\n")
    cmd = "Expand-Archive evil.zip -DestinationPath .venv\\Lib\\site-packages -Force"
    assert _cmd(st, folder, cmd) == "deny"
    with guard() as g:
        assert _ps(cmd, folder).returncode == 0
    _caught(g, site_dir, stopped, cards)


def test_scan_catches_git_clone_into_site_packages(hooks, tmp_path):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    git = shutil.which("git")
    if not git:
        pytest.skip("no git here")
    src = tmp_path / "srcrepo"
    src.mkdir()
    (src / "thing.pth").write_text("import os\n", encoding="utf-8")
    (src / "sitecustomize.py").write_text("print('x')\n", encoding="utf-8")
    ident = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    git_init(src, git=git)                # fails the test at once if init fails (conftest)
    for args in (["add", "."], [*ident, "commit", "-q", "-m", "x"]):
        assert _run([git, *args], str(src)).returncode == 0
    cmd = f'git clone "{src}" .venv/Lib/site-packages/thing'
    assert _cmd(st, folder, cmd) == "deny"
    with guard() as g:
        r = _run([git, "-c", "core.longpaths=true", "clone", "-q", "--template=", str(src),
                  ".venv/Lib/site-packages/thing"], folder)
        assert r.returncode == 0, r.stderr
    assert g.result["action"] == "stopped_try"
    names = sorted(os.path.basename(c["path"]) for c in g.result["try"])
    assert names == ["sitecustomize.py", "thing.pth"]
    assert not os.path.exists(os.path.join(site_dir, "thing", "thing.pth"))
    assert not os.path.exists(os.path.join(site_dir, "thing", "sitecustomize.py"))


def test_a_change_to_the_cores_own_python_means_safe_mode(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, core = hooks
    with guard() as g:
        _run([sys.executable, "-I", "-c", f"open(r'{core / 'x.pth'}', 'w').write('import os')"], folder)
    assert g.result["action"] == "safe_mode" and safe == ["core_python_hook_changed"]
    assert cards[-1]["kind"] == "core_hook_changed" and (core / "x.pth").exists(), "left in place as evidence"


def test_a_clean_command_passes_the_scan(hooks):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    with guard() as g:
        _run([sys.executable, "-I", "-c", "open('notes.txt', 'w').write('hi')"], folder)
    assert g.result["ok"] and stopped == [] and safe == [] and cards == []


def test_the_cores_own_site_packages_deny_destination_colon_and_expand_archive(places):
    import site as site_mod
    st, folder, _linked, _data = places
    core_site = site_mod.getsitepackages()[-1]
    for cmd in (f'Copy-Item a.pth -Destination:"{core_site}"', f'Copy-Item a.pth -destination:{core_site}',
                f'Expand-Archive a.zip -DestinationPath "{core_site}"', f'Expand-Archive a.zip "{core_site}"'):
        assert _cmd(st, folder, cmd) == "deny", cmd


def test_robocopy_and_xcopy_switches_are_not_paths(places):
    st, folder, _linked, _data = places
    for cmd in ("robocopy . out /s /E /R:1 /MIR", "xcopy a.txt out\\ /s /y", "robocopy src dst *.txt /XD node_modules",
                "dir /s /b", "copy /y a.txt b.txt"):
        assert _cmd(st, folder, cmd) != "deny", cmd


# -- re-check 6: pip's own RECORD lets a normal install's .pth through ------------------------------------

def _wheel(dest_dir, name="tinyhook", files=None):
    """A tiny wheel built here by hand (no network, no build tool): its files plus a real RECORD."""
    import base64
    import hashlib
    files = files or {f"{name}.pth": f"{name}_data\n"}
    dist = f"{name}-1.0.dist-info"
    files = dict(files)
    files[f"{dist}/METADATA"] = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
    files[f"{dist}/WHEEL"] = "Wheel-Version: 1.0\nGenerator: l4-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    rows = []
    for path, text in files.items():
        data = text.encode("utf-8")
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append(f"{path},sha256={digest},{len(data)}")
    rows.append(f"{dist}/RECORD,,")
    whl = os.path.join(dest_dir, f"{name}-1.0-py3-none-any.whl")
    with zipfile.ZipFile(whl, "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
        zf.writestr(f"{dist}/RECORD", "\n".join(rows) + "\n")
    return whl


def _pip_install(whl, site_dir, cwd):
    r = _run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
              "--disable-pip-version-check", "--no-compile", "--target", site_dir, whl], cwd)
    assert r.returncode == 0, r.stderr[-800:]


def test_a_pip_install_that_ships_a_pth_passes_the_scan(hooks, tmp_path):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    whl = _wheel(str(tmp_path))
    with guard() as g:
        _pip_install(whl, site_dir, folder)
    assert g.result["ok"] and g.result["action"] == "none", g.result
    assert [os.path.basename(c["path"]) for c in g.result["recorded"]] == ["tinyhook.pth"]
    assert os.path.exists(os.path.join(site_dir, "tinyhook.pth")) and stopped == [] and cards == []


def test_an_unrecorded_pth_beside_a_pip_install_still_stops_the_try(hooks, tmp_path):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    whl = _wheel(str(tmp_path))
    with guard() as g:
        _pip_install(whl, site_dir, folder)
        shutil.copy(os.path.join(folder, "evil.pth"), os.path.join(site_dir, "evil.pth"))   # dropped by copy
    assert g.result["action"] == "stopped_try" and stopped == ["python_hook_changed"]
    assert [os.path.basename(c["path"]) for c in g.result["try"]] == ["evil.pth"]
    assert not os.path.exists(os.path.join(site_dir, "evil.pth")), "the unrecorded one is undone"
    assert os.path.exists(os.path.join(site_dir, "tinyhook.pth")), "the recorded one stays"


def test_a_recorded_sitecustomize_or_a_wrong_hash_still_stops_the_try(hooks, tmp_path):
    st, folder, site_dir, guard, stopped, safe, cards, _core = hooks
    whl = _wheel(str(tmp_path), "custhook", {"sitecustomize.py": "print('x')\n"})
    with guard() as g:
        _pip_install(whl, site_dir, folder)
    assert g.result["action"] == "stopped_try", "sitecustomize never passes, recorded or not"
    assert not os.path.exists(os.path.join(site_dir, "sitecustomize.py"))
    stopped.clear()
    dist = os.path.join(site_dir, "forged-1.0.dist-info")
    with guard() as g:                                   # a hand-made record whose hash doesn't match
        os.makedirs(dist)
        with open(os.path.join(site_dir, "forged.pth"), "w", encoding="utf-8") as fh:
            fh.write("import os\n")
        with open(os.path.join(dist, "RECORD"), "w", encoding="utf-8") as fh:
            fh.write("forged.pth,sha256=AAAA,10\n")
    assert g.result["action"] == "stopped_try" and not os.path.exists(os.path.join(site_dir, "forged.pth"))


def test_a_recorded_pth_in_the_cores_own_python_still_means_safe_mode(hooks, tmp_path):
    st, folder, site_dir, guard, stopped, safe, cards, core = hooks
    whl = _wheel(str(tmp_path))
    with guard() as g:
        _pip_install(whl, str(core), folder)
    assert g.result["action"] == "safe_mode" and safe == ["core_python_hook_changed"]


# -- re-check 7: changes made while the vault is locked (D54) ---------------------------------------------

def _snap(paths):
    return {p: p.read_bytes() for p in paths if p.exists()}


def _restore(saved, paths):
    for p in paths:
        if p in saved:
            p.write_bytes(saved[p])
        elif p.exists():
            p.unlink()


def _closed_with_card(z, what):
    assert z.bridge.posture()["state"] == "closed"
    cards = [c for c in z.cards if c["kind"] == "vault_mismatch"]
    assert cards and what in cards[-1]["what"], [c for c in z.cards]


def test_a_revoke_while_locked_then_the_old_consent_file_is_caught_at_unlock(state):
    arch = Arch(state)
    z = _z(state, arch)
    rec = grant(z, manifest(hosts=["api.example.com"]))
    files = [state / "consent.db"]
    old = _snap(files)
    arch.lock()
    assert z.consents.revoke(rec["consent_id"])                # generation 2, waits as pending
    _restore(old, files)                                        # the old file put back before the unlock
    assert z.consents.granted(rec["consent_id"]), "on its own the old file passes"
    arch.unlock()
    _closed_with_card(z, "consent.generation")


def test_a_grant_while_locked_then_the_old_policy_is_caught_at_unlock(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    d = state / "settings" / "bridge"
    files = [d / "policy.json", d / "policy.version"]
    old = _snap(files)
    arch.lock()
    grant(z, manifest("other", hosts=["api.other.org"]))        # a policy change while locked
    _restore(old, files)
    arch.unlock()
    _closed_with_card(z, "bridge.policy")


def test_ledger_lines_while_locked_then_the_old_ledger_pair_is_caught_at_unlock(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    z.bridge.request("pkg:freshtable", "api.example.com")
    logs = state / "logs"
    files = [logs / "outside.jsonl", logs / "outside.jsonl.head"]
    old = _snap(files)
    arch.lock()
    for _ in range(3):
        z.bridge.request("pkg:freshtable", "api.example.com")  # lines while locked
    _restore(old, files)                                        # the old log AND its seal
    arch.unlock()
    _closed_with_card(z, "bridge.ledger")


def test_a_restart_while_locked_keeps_pending_and_catches_the_old_files_at_start(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    d = state / "settings" / "bridge"
    files = [d / "policy.json", d / "policy.version"]
    old = _snap(files)
    arch.lock()
    grant(z, manifest("other", hosts=["api.other.org"]))
    z.stop()                                                     # the Core stops while locked
    _restore(old, files)
    z2 = _z(state, arch)
    assert "bridge.policy" in z2.vault.pending, "pending survived the restart"
    _closed_with_card(z2, "bridge.policy")                       # a card at start (09 section 3)
    arch.unlock()
    assert z2.bridge.posture()["state"] == "closed"


def test_a_deleted_pending_file_on_a_bot_that_has_run_is_a_card_at_start(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    arch.lock()
    z.stop()
    (state / "settings" / "bridge" / "vault_pending.json").unlink()
    z2 = _z(state, arch)
    _closed_with_card(z2, "vault_pending")


def test_a_clean_locked_window_and_restart_stay_open(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    arch.lock()
    grant(z, manifest("other", hosts=["api.other.org"]))
    z.bridge.request("pkg:other", "api.other.org")
    z.stop()
    z2 = _z(state, arch)
    assert z2.bridge.posture()["state"] == "open" and "vault_mismatch" not in [c["kind"] for c in z2.cards]
    arch.unlock()
    assert z2.bridge.posture()["state"] == "open" and z2.vault.pending == {}
    assert arch.kept("bridge.policy")["version"] == z2.bridge.policy.version


def test_after_a_mismatch_she_opens_again_and_the_next_unlock_is_clean(state):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))
    files = [state / "logs" / "outside.jsonl", state / "logs" / "outside.jsonl.head"]
    z.bridge.request("pkg:freshtable", "api.example.com")
    old = _snap(files)
    arch.lock()
    z.bridge.request("pkg:freshtable", "api.example.com")
    _restore(old, files)
    arch.unlock()
    _closed_with_card(z, "bridge.ledger")
    arch.lock()
    arch.unlock()
    assert len([c for c in z.cards if c["kind"] == "vault_mismatch"]) == 2, "closed again until she opens"
    z.bridge.open()                                               # she checked and opens again
    arch.lock()
    arch.unlock()
    assert z.bridge.posture()["state"] == "open"
    assert len([c for c in z.cards if c["kind"] == "vault_mismatch"]) == 2


def test_a_check_that_breaks_at_unlock_closes_the_bridge_with_a_card(state, monkeypatch):
    arch = Arch(state)
    z = _z(state, arch)
    grant(z, manifest(hosts=["api.example.com"]))

    def broken(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(z.vault, "unlocked", broken)
    arch.lock()
    arch.unlock()
    assert z.bridge.posture()["state"] == "closed"
    assert [c for c in z.cards if c["kind"] == "vault_mismatch"][-1]["why"] == ["cant_confirm"]
