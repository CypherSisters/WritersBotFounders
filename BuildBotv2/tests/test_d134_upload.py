"""D134: send a file from the device she is sitting at. Real routes, and a real listener on the stand-in where a socket is the
point. Never a real address: the guard in conftest.py stays.
"""

from __future__ import annotations

import base64
import http.client
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from conftest import HELLO
from puffpai_core import pairing, send_file
from puffpai_core.api import auth, network
from puffpai_core.zone1 import uploads
from puffpai_core.zone1.folders import Library, canon
from test_lan_pairing import ADDRESS, PEER, STAND_IN, Device, _add, _free_port, local

REPO = Path(__file__).resolve().parents[1]
BODY = b"Chapter one.\nSomething plain.\n"
UPLOAD = "/v1/knowledge/upload"


@pytest.fixture
def on(core):
    n = core.app.extensions[network.EXTENSION]
    n.addresses = lambda: {ADDRESS}
    n.names = lambda: {"mybox", "mybox.local"}
    network.save_on(core.state, True)
    return n


@pytest.fixture
def phone(core, on):
    d = Device(core.app)
    assert d.pair(pairing.new_code(core.state)).status_code == 200
    return d


def send(d, name, data=BODY, *, query=None, cookie=True, second=True, origin=True, headers=None, peer=None, **kw):
    """The upload as a browser's fetch sends it: raw bytes, the name in the query."""
    h = {"Content-Type": "application/octet-stream", **(headers or {})}
    if cookie and d.cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in d.cookies.items())
    if second and d.second:
        h[auth.DEVICE_HEADER] = d.second
    if origin:
        h.setdefault("Origin", d.origin)
        h.setdefault("X-PuffPAI", "1")
    env = {"puffpai.listener": network.NETWORK_LISTENER, "REMOTE_ADDR": peer or d.peer}
    q = {"name": name} if query is None else query
    return d.client.post(UPLOAD, base_url=f"http://{d.host}", query_string=q, data=data, headers=h, environ_base=env, **kw)


def folder(core):
    return Path(core.state) / "data" / uploads.FOLDER


def lib(core):
    from puffpai_core.zone1.archivist import for_state
    return Library(for_state(core.state, core.bus), core.state)


# -- it arrives ------------------------------------------------------------------------------------------------------
def test_a_paired_device_sends_a_file_and_it_is_saved_in_one_place(core, phone):
    r = send(phone, "My Novel.txt")
    assert r.status_code == 200 and r.json["ok"] is True and r.headers["Cache-Control"] == "no-store"
    assert r.json["replaced"] is False and r.json["pinned"] is False and r.json["size"] == len(BODY)
    assert r.json["line"] == "My Novel.txt is saved on your bot."
    saved = folder(core) / "My Novel.txt"
    assert saved.read_bytes() == BODY and canon(r.json["path"]) == canon(saved)
    assert [p.name for p in folder(core).iterdir()] == ["My Novel.txt"]         # no half file left behind


def test_the_local_owner_may_send_too(core, on):
    key = core.tokens.issue_device("a device")
    r = core.app.test_client().post(UPLOAD, query_string={"name": "a.md"}, data=BODY, headers=local(key))
    assert r.status_code == 200 and (folder(core) / "a.md").read_bytes() == BODY


def test_a_second_upload_replaces_the_first_whole_and_says_so(core, phone):
    assert send(phone, "a.txt", b"old words").json["replaced"] is False
    r = send(phone, "a.txt", b"my saved changes")
    assert r.status_code == 200 and r.json["replaced"] is True and "replaces your earlier copy" in r.json["line"]
    assert (folder(core) / "a.txt").read_bytes() == b"my saved changes"
    assert [p.name for p in folder(core).iterdir()] == ["a.txt"]


def test_an_uploaded_copy_pins_and_sends_to_a_package(core, phone, monkeypatch):
    got = send(phone, "doc.txt").json
    r = phone.open("POST", "/v1/knowledge/pinned", body={"path": got["path"]})
    assert r.status_code == 200 and any(x["name"] == "doc.txt" for x in r.json["pinned"])
    sent = []
    monkeypatch.setattr(send_file, "hand", lambda core, review, pid, name, data: sent.append((pid, name, data)) or "It has it.")
    r = phone.open("POST", "/v1/packages/some-app/send", body={"path": got["path"]})
    assert r.status_code == 200 and r.json["line"] == "It has it."
    assert sent == [("some-app", "doc.txt", BODY)]
    # Sent again: still pinned, says so, and the Core reads the new words without a second pin.
    r = send(phone, "doc.txt", b"Newer words for the chapter.\n")
    assert r.json["pinned"] is True and r.json["replaced"] is True
    assert "Newer words" in lib(core).indexed_text(r.json["path"])


# -- bad names -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "../evil.txt", "..\\evil.txt", "a/../../evil.txt", "/etc/evil.txt", "C:\\Windows\\evil.txt", "C:evil.txt", "\\\\host\\share\\evil.txt",
    "nul\x00.txt", "a\x00b.txt", "a\nb.txt", "a\tb.txt", "a\u202eb.txt", "CON", "con.txt", "CON.txt", "Nul.md", "prn.pdf", "aux.docx", "COM1.txt", "lpt9.tar.txt",
    "a" * 200 + ".txt", "\u00e9" * 119 + ".txt", ".", "..", "...", "", "   ", " a.txt", "a.txt ", "a.txt.", ".hidden.txt", "a:b.txt", "a.txt:stream", "a*.txt", "a?.txt", 'a".txt', "a<.txt", "a|.txt",
    "keys.pem", "novel", "a.exe", "a.txt.exe", "evil.PNG", ".env", "x.part",
])
def test_a_bad_name_saves_nothing(core, phone, name):
    r = send(phone, name)
    assert r.status_code == 400 and r.json["code"] in ("name", "kind"), repr(name)
    assert not folder(core).exists() or list(folder(core).iterdir()) == []
    data = Path(core.state) / "data"
    assert not (data / "evil.txt").exists() and not (Path(core.state) / "evil.txt").exists()


@pytest.mark.parametrize("query", [{}, {"name": ""}])
def test_no_name_saves_nothing(core, phone, query):
    assert send(phone, "x", query=query).status_code == 400


def test_a_path_from_the_client_is_never_used(core, phone, tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("hers", encoding="utf-8")
    for q in ({"name": str(target)}, {"name": "ok.txt", "path": str(target), "folder": str(tmp_path)}):
        r = send(phone, "x", query=q)
        if r.status_code == 200:
            assert canon(r.json["path"]) == canon(folder(core) / "ok.txt")
    assert target.read_text(encoding="utf-8") == "hers"


def test_a_plain_name_with_other_letters_is_kept(core, phone):
    r = send(phone, "Caf\u00e9 notes (2).docx", b"PK not really a docx")
    assert r.status_code == 200 and r.json["name"] == "Caf\u00e9 notes (2).docx"


def test_an_empty_file_saves_nothing(core, phone):
    r = send(phone, "a.txt", b"")
    assert r.status_code == 400 and r.json["code"] == "empty" and not (folder(core) / "a.txt").exists()


# -- the place can't be turned into somewhere else ----------------------------------------------------------------------
@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="no links here")
def test_a_link_planted_in_its_place_is_refused(core, phone, tmp_path):
    (Path(core.state) / "data").mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        if os.name == "nt":                                  # a junction needs no special right on Windows
            import _winapi
            _winapi.CreateJunction(str(outside), str(folder(core)))
        else:
            folder(core).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account can't make links")
    r = send(phone, "a.txt")
    assert r.status_code == 400 and list(outside.iterdir()) == []


def test_an_uploaded_name_cant_reach_the_vault_by_a_data_setting_pointed_at_it(core, phone):
    """Her data setting naming the vault: the same check as a pinned file refuses it."""
    settings = Path(core.state) / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    vault = Path(core.state) / "vault"
    vault.mkdir(exist_ok=True)
    (settings / "folders.json").write_text('{"data": "' + str(vault).replace("\\", "\\\\") + '"}', encoding="utf-8")
    r = send(phone, "a.txt")
    assert r.status_code == 400 and not (vault / uploads.FOLDER).exists()


# -- too big ---------------------------------------------------------------------------------------------------------
def post_stream(d, s, content_type="application/octet-stream", declared=None):
    """A POST whose body is *s*, as a server hands it over: waitress's stream, with or without a declared size."""
    h = {"Cookie": "; ".join(f"{k}={v}" for k, v in d.cookies.items()), auth.DEVICE_HEADER: d.second,
         "Origin": d.origin, "X-PuffPAI": "1", "Content-Type": content_type}
    env = {"wsgi.input": s, "wsgi.input_terminated": True}
    if declared is not None:
        env["CONTENT_LENGTH"] = str(declared)
    return d.client.post(UPLOAD, base_url=f"http://{d.host}", query_string={"name": "a.txt"}, headers=h, environ_overrides=env,
                         environ_base={"puffpai.listener": network.NETWORK_LISTENER, "REMOTE_ADDR": PEER})


class Stream:
    """A body that counts what is read, and never ends by itself."""

    def __init__(self):
        self.read_total = 0

    def read(self, n=-1):
        n = n if n and n > 0 else 1 << 16
        self.read_total += n
        return b"x" * n


def test_a_declared_size_over_the_limit_is_refused_without_reading_it(core, phone):
    s = Stream()
    r = post_stream(phone, s, declared=uploads.UPLOAD_MAX + 1)
    assert r.status_code == 413 and r.json["code"] == "too_big" and "50 MB" in r.json["error"]
    assert s.read_total == 0 and not (folder(core) / "a.txt").exists()


def test_a_body_with_no_declared_size_stops_at_the_limit(core, phone, monkeypatch):
    monkeypatch.setattr(uploads, "UPLOAD_MAX", 3 * 1024 * 1024)
    s = Stream()
    r = post_stream(phone, s)
    assert r.status_code == 413
    assert s.read_total <= uploads.UPLOAD_MAX + 1                       # read only up to one byte past it
    assert not (folder(core) / "a.txt").exists() and list(folder(core).iterdir()) == []


def test_a_too_big_copy_leaves_the_first_one_as_it_was(core, phone, monkeypatch):
    assert send(phone, "a.txt", b"first").status_code == 200
    monkeypatch.setattr(uploads, "UPLOAD_MAX", 10)
    assert send(phone, "a.txt", b"x" * 11).status_code == 413
    assert (folder(core) / "a.txt").read_bytes() == b"first" and len(list(folder(core).iterdir())) == 1


def test_a_json_typed_body_is_not_read_by_the_gate(core, phone):
    """The network gate looks for a PIN in a JSON body. Not on this route: it reads up to its limit, itself."""
    s = Stream()
    r = post_stream(phone, s, "application/json", uploads.UPLOAD_MAX + 1)
    assert r.status_code == 413 and s.read_total == 0


def test_the_real_network_listener_refuses_a_huge_declared_size_from_its_headers(core, on):
    """Waitress holds a body until it is whole, so the listener itself must stop this. No body is ever sent here."""
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.peer_ok = (lambda: {STAND_IN}), (lambda a: True)
    assert on.open() is True
    try:
        with socket.create_connection((STAND_IN, on.port), timeout=10) as s:
            s.sendall((f"POST {UPLOAD}?name=a.txt HTTP/1.1\r\nHost: {STAND_IN}:{on.port}\r\n"
                       f"Content-Length: {50 * 1024 ** 3}\r\nContent-Type: application/octet-stream\r\n\r\n").encode())
            answer = s.recv(200)
        assert answer.startswith(b"HTTP/1.1 413")
    finally:
        on.close()
    assert network.MAX_REQUEST_BYTES >= send_file.MAX_SEND * 4 // 3 + 1024      # a package send still fits
    assert network.MAX_REQUEST_BYTES >= uploads.UPLOAD_MAX


def test_a_real_upload_over_a_real_socket(core, on):
    """Both halves over the wire, and a device the same real listener turns away without them."""
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.names, on.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    assert on.open() is True
    origin = f"http://{STAND_IN}:{on.port}"

    def call(method, path, headers, body):
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=15)
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data
    try:
        import json
        code = pairing.new_code(core.state)
        r, data = call("POST", "/v1/pair", {"Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/json"}, json.dumps({"code": code}))
        assert r.status == 200
        h = {"Cookie": r.getheader("Set-Cookie").split(";", 1)[0], auth.DEVICE_HEADER: json.loads(data)["device_key"],
             "Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/octet-stream"}
        r, data = call("POST", UPLOAD + "?name=real.txt", h, BODY)
        assert r.status == 200 and json.loads(data)["size"] == len(BODY)
        assert (folder(core) / "real.txt").read_bytes() == BODY
        r, _ = call("POST", UPLOAD + "?name=real2.txt", {k: v for k, v in h.items() if k != auth.DEVICE_HEADER}, BODY)
        assert r.status == 401 and not (folder(core) / "real2.txt").exists()
    finally:
        on.close()


# -- who may ---------------------------------------------------------------------------------------------------------
def test_an_unpaired_device_or_a_cookie_without_its_half_saves_nothing(core, phone):
    assert send(Device(core.app, peer="192.168.1.60"), "a.txt").status_code == 401
    r = send(phone, "a.txt", second=False)
    assert r.status_code == 401 and r.json["code"] == "sign_in"
    assert send(phone, "a.txt", cookie=False).status_code == 401
    assert not folder(core).exists()


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example", "X-PuffPAI": "1"}, {"Origin": "null", "X-PuffPAI": "1"},
    {"Origin": f"http://{ADDRESS}:19100", "X-PuffPAI": "1"},           # a package page on another port
    {"Origin": "http://mybox.local:9", "X-PuffPAI": "1"}, {"X-PuffPAI": "0", "Origin": "x"}, {"X-PuffPAI": "1"},
])
def test_a_wrong_origin_saves_nothing(core, phone, headers):
    r = send(phone, "a.txt", origin=False, headers=headers)
    assert r.status_code == 403 and not folder(core).exists()


def test_a_public_peer_a_wrong_host_and_every_other_key_save_nothing(core, phone):
    assert send(phone, "a.txt", peer="8.8.8.8").status_code == 403
    assert send(phone, "a.txt", peer="100.100.2.2").status_code == 403        # a VPN address
    d = Device(core.app, host=f"evil.example:{phone.net.port}")
    d.cookies, d.second = phone.cookies, phone.second
    assert send(d, "a.txt").status_code == 421
    for token in (core.tokens.issue_owner_key(), core.tokens.issue_package_token("some-app"), core.tokens.issue_toolsmith("b1")):
        assert send(Device(core.app), "a.txt", headers=local(token)).status_code == 403
        r = core.app.test_client().post(UPLOAD, query_string={"name": "a.txt"}, data=BODY, headers=local(token))
        assert r.status_code == 403, "on this computer too: an owner API key or a token is no paired device"
    assert not folder(core).exists()


def test_a_try_or_an_adopted_app_gets_nothing_new(core, phone, tmp_path):
    """A page port forwards to its package, never to the Core's routes: an upload path there is only the package's path."""
    _add(core, tmp_path, "outside-app", "adopted:https://example.test/x")
    r = core.app.test_client(use_cookies=False).post(
        UPLOAD, base_url=f"http://{ADDRESS}:19999", query_string={"name": "a.txt"}, data=BODY,
        environ_base={"puffpai.listener": network.NETWORK_PAGE_LISTENER, network.NETWORK_PACKAGE: "outside-app", "REMOTE_ADDR": PEER})
    assert r.status_code == 404 and not folder(core).exists()


def test_the_route_is_in_the_contract_as_a_paired_devices(core):
    import yaml
    spec = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
    assert spec["paths"][UPLOAD]["post"]["x-access"] == "owner_device"
    assert getattr(core.app.view_functions["knowledge.knowledge_upload"], "puffpai_access") == "owner_device"


# -- the picker's answer ---------------------------------------------------------------------------------------------
def test_the_picker_answers_upload_from_a_device_and_opens_no_dialog(core, phone, monkeypatch):
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: pytest.fail("a dialog opened on the bot's computer"))
    r = phone.open("POST", "/v1/knowledge/pick", body={"kind": "file"})
    assert r.status_code == 200 and r.json["upload"] is True and r.json["typed"] is True and r.json["path"] == ""
    assert "this device" in r.json["line"] and "bot's" not in r.json["line"]
    r = phone.open("POST", "/v1/knowledge/pick", body={"kind": "folder"})
    assert r.status_code == 200 and "upload" not in r.json and r.json["typed"] is True
    assert "on your bot" in r.json["line"]


def test_the_picker_answers_upload_on_a_bot_with_no_dialog(core, on, monkeypatch):
    """A Linux bot, in a browser on its own screen: no Windows dialog, so the same upload."""
    monkeypatch.setattr(send_file, "can_pick", lambda: False)
    key = core.tokens.issue_device("a device")
    r = core.app.test_client().post("/v1/knowledge/pick", json={"kind": "file"}, headers=local(key), environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.json["upload"] is True


def test_on_windows_at_the_bots_own_computer_the_dialog_stays(core, on, monkeypatch):
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: "C:/picked.txt")
    key = core.tokens.issue_device("a device")
    r = core.app.test_client().post("/v1/knowledge/pick", json={"kind": "file"}, headers=local(key), environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200 and r.json == {"path": "C:/picked.txt"}


# -- item 4: package send, and typed paths ------------------------------------------------------------------------------
def test_package_send_with_bytes_works_on_the_network_with_both_halves_only(core, phone, monkeypatch):
    sent = []
    monkeypatch.setattr(send_file, "hand", lambda core, review, pid, name, data: sent.append((pid, name, data)) or "It has it.")
    body = {"file_name": "a.txt", "content_b64": base64.b64encode(BODY).decode("ascii")}
    r = phone.open("POST", "/v1/packages/some-app/send", body=body)
    assert r.status_code == 200 and sent == [("some-app", "a.txt", BODY)]
    assert phone.open("POST", "/v1/packages/some-app/send", body=body, second=False).status_code == 401
    r = phone.open("POST", "/v1/packages/some-app/send", body=body, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"})
    assert r.status_code == 403
    assert len(sent) == 1


def test_a_typed_path_in_a_package_send_names_only_a_pinned_or_linked_file(core, phone, monkeypatch, tmp_path):
    monkeypatch.setattr(send_file, "hand", lambda *a: pytest.fail("a file went to a package"))
    secret = tmp_path / "private notes.txt"
    secret.write_text("the secret words", encoding="utf-8")
    vault = Path(core.state) / "vault"
    vault.mkdir(exist_ok=True)
    (vault / "x.txt").write_text("vault", encoding="utf-8")
    for path in (str(secret), str(vault / "x.txt"), str(core.state / "tokens.db"), "/etc/passwd", "..\\..\\x.txt", str(folder(core) / "not-pinned.txt")):
        r = phone.open("POST", "/v1/packages/some-app/send", body={"path": path})
        assert r.status_code == 400 and "secret words" not in r.get_data(as_text=True), path
    # An uploaded copy that isn't pinned is no exception to the rule.
    got = send(phone, "not-pinned.txt").json
    assert phone.open("POST", "/v1/packages/some-app/send", body={"path": got["path"]}).status_code == 400


def test_pin_and_link_by_a_typed_path_never_hand_back_contents(core, phone, tmp_path):
    words = "the unique words of this file"
    secret = tmp_path / "notes.txt"
    secret.write_text(words, encoding="utf-8")
    r = phone.open("POST", "/v1/knowledge/pinned", body={"path": str(secret)})
    assert r.status_code == 200 and words not in r.get_data(as_text=True)      # it names a file on the bot; no words back
    other = tmp_path / "keys.pem"
    other.write_text(words, encoding="utf-8")
    r = phone.open("POST", "/v1/knowledge/pinned", body={"path": str(other)})
    assert r.status_code == 400 and words not in r.get_data(as_text=True)
    r = phone.open("POST", "/v1/knowledge/pinned", body={"path": str(core.state / "tokens.db")})
    assert r.status_code == 400
    r = phone.open("POST", "/v1/folders/link", body={"path": str(tmp_path)})
    assert words not in r.get_data(as_text=True)
    assert words not in phone.get("/v1/knowledge/pinned").get_data(as_text=True)
    assert words not in phone.get("/v1/folders").get_data(as_text=True)


# -- the Desktop ------------------------------------------------------------------------------------------------------
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_desktop_side():
    proc = subprocess.run([NODE, str(REPO / "tests" / "js" / "d134.test.js")], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_changed_desktop_files_parse():
    web = REPO / "packages" / "desktop" / "web"
    for name in ("logic.js", "desktop.js", "window.js", "sendfile.js", "pages/knowledge.js"):
        proc = subprocess.run([NODE, "--check", str(web / name)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (name, proc.stderr)
