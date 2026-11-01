"""Regression tests for the L6 third re-check: no redirects, every cd form, no shells or system
variables in a saved run line, and leases ended on a key error."""

import io
import json
import urllib.request
import urllib.response
from email.message import Message

import pytest

from toolsmith_fakes import FakePorts
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.zone3.toolsmith import adopt
from puffpai_core.zone3.toolsmith.errors import CloudError
from puffpai_core.zone3.toolsmith_host import sender
from puffpai_core.zone3.toolsmith_host.ports import Ports, Redirected

KEY = "sk-ant-" + "B" * 40
PORT = 18555


class Recorded(urllib.request.HTTPSHandler):
    """A recorded HTTPS world: the company's host answers 302 to another host; nothing leaves this test."""

    def __init__(self, first_status=302):
        super().__init__()
        self.seen = []
        self.first_status = first_status

    def https_open(self, req):
        self.seen.append({"url": req.full_url, "headers": dict(req.header_items())})
        headers = Message()
        if req.host == "api.anthropic.com":
            headers["Location"] = "https://collector.example.com/steal"
            code, body = self.first_status, b""
        else:
            code, body = 200, b'{"ok": true}'
        resp = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, code)
        resp.msg = "x"
        return resp


class RecordedPorts(Ports):
    def __init__(self, state, handler):
        super().__init__(state)
        self.handler = handler

    def _handlers(self):
        return (self.handler,)

    def bridge_decision(self, subject, host, phase="build"):
        return "allow"                                   # even a Bridge open to every host gets no redirect

    def lend_key(self, key_name, borrower, purpose, *, hosts=(), job=None):
        return "lease-1"

    def key_value(self, handle, *, borrower, purpose, host=None):
        return KEY

    def screen_outbound(self, text):
        return text

    def end_lease(self, handle):
        self.ended = handle


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_the_core_never_follows_a_redirect(state, status):
    rec = Recorded(status)
    p = RecordedPorts(state, rec)
    with pytest.raises(Redirected) as e:
        p.http_send("POST", "https://api.anthropic.com/v1/messages", subject="toolsmith:cloud",
                    headers={"x-api-key": KEY}, body=b"{}")
    assert e.value.status == status and "answered with a redirect, so nothing more was sent" in str(e.value)
    assert [x["url"] for x in rec.seen] == ["https://api.anthropic.com/v1/messages"]   # nothing further


def test_the_recorded_world_does_redirect_with_a_plain_opener(state):
    """The test's own check: urllib's default opener would follow it, key and all."""
    rec = Recorded()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), rec)
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", headers={"x-api-key": KEY})
    with opener.open(req, timeout=5):
        pass
    assert rec.seen[-1]["url"].startswith("https://collector.example.com") and KEY in json.dumps(rec.seen[-1])


def test_a_redirect_from_the_company_ends_the_call_and_the_key_never_moves(state, tmp_path):
    rec = Recorded()
    p = RecordedPorts(state, rec)
    prov = sender.load_providers()["anthropic"]
    s = sender.FrontierSender(p, prov, model="claude-opus-5-5", build_id="b1", cap_usd=5,
                              ledger=sender.Ledger(tmp_path / "u.json"), consent_given=sender.consent_sha(prov))
    with pytest.raises(CloudError) as e:
        s.turn("sys", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}], [])
    assert e.value.code == "redirect" and "answered with a redirect" in e.value.message
    assert all("collector.example.com" not in x["url"] for x in rec.seen)
    assert len(rec.seen) == 1                                   # no retry, no second host


# -- leases end on a key error, not only at the cap ------------------------------------

def _sender(tmp_path, ports):
    prov = sender.load_providers()["anthropic"]
    return sender.FrontierSender(ports, prov, model="claude-opus-5-5", build_id="b1", cap_usd=5,
                                 ledger=sender.Ledger(tmp_path / "u.json"), consent_given=sender.consent_sha(prov))


def test_a_401_ends_the_lease(tmp_path):
    ports = FakePorts(tmp_path)
    ports.keys = {"toolsmith.anthropic": KEY}
    ports.http_send_fn = lambda *a: (401, {}, b"{}")
    s = _sender(tmp_path, ports)
    with pytest.raises(CloudError) as e:
        s.turn("sys", [{"role": "user", "content": [{"type": "text", "text": "hi"}]}], [])
    assert e.value.code == "key"
    (lease,) = ports.leases.values()
    assert lease["ended"] and s._handle is None


def test_a_refused_lease_is_ended_before_it_is_dropped(tmp_path):
    ports = FakePorts(tmp_path)
    ports.keys = {"toolsmith.anthropic": KEY}
    s = _sender(tmp_path, ports)
    s._lend()
    (handle,) = ports.leases
    ports.leases[handle]["hosts"] = ("other.example.com",)     # the lease no longer covers this host
    with pytest.raises(Exception):
        s._key("api.anthropic.com")
    assert ports.leases[handle]["ended"] and s._handle is None


# -- every cd form is cwd; shells and system variables are never saved -----------------------

@pytest.mark.parametrize("start", ["cd /d web && node s.js", "chdir web && node s.js", "CD web && node s.js",
                                   "Set-Location -Path web && node s.js", "Set-Location web && node s.js",
                                   "sl web && node s.js", "pushd web && node s.js", "cd /D web && node s.js"])
def test_every_cd_form_is_saved_as_cwd(start):
    block, notes, unsaved = adopt.run_block(start, [], PORT)
    assert block["cwd"] == "web" and block["start"] == ["{node}", "s.js"] and not notes and not unsaved


@pytest.mark.parametrize("start", ["cd && node s.js", "cd - && node s.js", "cd ~ && node s.js", "cd a b && node s.js",
                                   "cd /d C:\\Windows && node s.js", "Set-Location -Stack x && node s.js",
                                   "cmd /c node s.js", "cmd.exe /c node s.js", "powershell -c node s.js",
                                   "powershell -Command node s.js", "powershell -EncodedCommand AAAA",
                                   "pwsh -c node s.js", "pwsh s.ps1", "bash -c 'node s.js'",
                                   "C:/Windows/System32/cmd.exe /c x", "start node s.js",
                                   "set PATH=C:/x&& node s.js", "PATH=/x node s.js", "PATHEXT=.X node s.js",
                                   "COMSPEC=x node s.js", "SystemRoot=x node s.js", "PYTHONPATH=x python app.py",
                                   "PYTHONHOME=x python app.py", "NODE_OPTIONS=--require=x node s.js"])
def test_shells_unknown_cds_and_system_variables_are_never_saved(start):
    with pytest.raises(adopt.Unsaved) as e:
        adopt.run_block(start, [], PORT)
    assert e.value.reason and e.value.lines == [start]


def test_a_system_variable_in_a_setup_line_is_said_and_listed():
    block, notes, unsaved = adopt.run_block("npm start", ["set NODE_OPTIONS=--x&& npm install", "npm install"], PORT)
    assert block["setup"] == [["npm", "install"]] and unsaved == ["set NODE_OPTIONS=--x&& npm install"]
    assert "NODE_OPTIONS, a system setting a package may never change" in notes[0]


@pytest.mark.parametrize("name", ["PATH", "path", "PathExt", "COMSPEC", "SystemRoot", "PYTHONPATH", "PYTHONHOME",
                                  "NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES"])
def test_the_schema_refuses_system_variables_in_env(name):
    man = {"schema": "puffpai-package/1", "id": "app", "kind": "app", "name": "App", "version": "0.1.0",
           "description": "An app.", "author": "x", "license": "MIT",
           "run": {"windows": {"type": "command", "start": ["{node}", "s.js"], "env": {name: "x"}}},
           "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
           "ui": {"pages": [{"id": "main", "title": "App", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
           "consent": "App is an outside app."}
    assert manifest_mod.validate(man) != []
    man["run"]["windows"]["env"] = {"PORT": "{port}", "API_PATH": "/api"}       # a name that only contains PATH
    assert manifest_mod.validate(man) == []
