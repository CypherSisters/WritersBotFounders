"""Lane I18c: what the owner saw on her test3 Desktop.

1. AlarmFlow's page showed raw JSON (``{"error":"AlarmFlow is not running. Start it first."}``): she opened
   it while it was still being added (its venv took 40 s), and the frame never asked again. A page opened in
   a frame now gets one plain line ("AlarmFlow is starting...") that asks again by itself; a widget's or a
   page's own fetch still gets JSON.
2. AlarmFlow's and Trivia's icons were broken: ``/icons/alarm.svg`` on the app's origin went to the app's own
   server, which has no such file (404). The Core now serves a server app's declared pictures from its folder.
3. example-page's install was left half done: its folder was in packages, with no record, after Windows
   refused to replace installs.json while it was open. Its writes now wait and try again, and an install
   whose record can't be written takes its folder back out.
"""

import json
import os
import shutil
import socket
import time

import pytest

from conftest import record_install
from puffpai_core.api import create_app
from puffpai_core.desktop import builtin_dir
from puffpai_core.packages import installs as installs_mod
from puffpai_core.packages.installs import Installs
from puffpai_core.steward.steward import Running

PAGES = {"puffpai.listener": "pages"}
FRAME = {"Sec-Fetch-Dest": "iframe", "Accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def app(state):
    return create_app(state)


def _add(app, state, pid, status):
    core = app.extensions["puffpai"]
    folder = state / "packages" / pid
    shutil.copytree(builtin_dir() / pid, folder)
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    record_install(core, folder, manifest, "core", status=status)
    return core


def _get(app, pid, path, headers=None):
    return app.test_client().get(path, environ_base=PAGES, base_url=f"http://{pid}.localhost:8422",
                                 headers=headers or {})


def _closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# -- 1. the page while its app starts ------------------------------------------------------------------
def test_a_page_opened_while_its_app_is_being_added_says_starting_and_asks_again(app, state):
    _add(app, state, "alarmflow", "installing")
    r = _get(app, "alarmflow", "/", FRAME)
    assert r.status_code == 503 and r.mimetype == "text/html"
    body = r.get_data(as_text=True)
    assert "AlarmFlow is starting..." in body
    assert '<meta http-equiv="refresh"' in body                   # it asks again by itself
    assert "{" not in body.split("<body>", 1)[1]                  # never raw JSON
    assert r.headers["Cache-Control"] == "no-store"


def test_a_stopped_or_broken_app_says_so_in_plain_words(app, state):
    core = _add(app, state, "alarmflow", "stopped")
    body = _get(app, "alarmflow", "/", FRAME).get_data(as_text=True)
    assert "AlarmFlow is not running. Start it from Apps." in body and 'http-equiv="refresh"' in body
    core.registry.update("alarmflow", status="broken")
    body = _get(app, "alarmflow", "/", FRAME).get_data(as_text=True)
    assert "AlarmFlow is not working right now. Open Apps to see why." in body


def test_a_fetch_or_a_widget_still_gets_json(app, state):
    _add(app, state, "alarmflow", "installing")
    r = _get(app, "alarmflow", "/widget/running", {"Sec-Fetch-Dest": "empty", "Accept": "application/json"})
    assert r.status_code == 503 and r.json == {"error": "AlarmFlow is starting..."}
    r = _get(app, "alarmflow", "/data")                           # no browser headers at all
    assert r.status_code == 503 and r.is_json


def test_an_app_that_started_but_does_not_listen_yet_is_starting(app, state):
    core = _add(app, state, "alarmflow", "running")
    core.steward._running["alarmflow"] = Running("alarmflow", "static", _closed_port(), "tok", time.time())
    try:
        r = _get(app, "alarmflow", "/", FRAME)
        assert r.status_code == 503 and "AlarmFlow is starting..." in r.get_data(as_text=True)
        core.steward._running["alarmflow"].started_at = time.time() - 600     # long past its start
        r = _get(app, "alarmflow", "/", FRAME)
        assert r.status_code == 502 and "AlarmFlow did not answer. Open Apps to see why." in r.get_data(as_text=True)
        assert _get(app, "alarmflow", "/data").is_json                     # a fetch: JSON as before
    finally:
        core.steward._running.pop("alarmflow", None)


# -- 2. a server app's icons ---------------------------------------------------------------------------
@pytest.mark.parametrize("pid,icon", [("alarmflow", "icons/alarm.svg"), ("trivia", "icons/question.svg")])
def test_a_server_apps_icon_comes_from_its_folder(app, state, pid, icon):
    core = _add(app, state, pid, "stopped")
    want = (state / "packages" / pid / icon).read_bytes()
    r = _get(app, pid, "/" + icon, {"Sec-Fetch-Dest": "image"})
    assert r.status_code == 200 and r.mimetype == "image/svg+xml" and r.data == want
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    # running too (its server would answer 404): still the Core's copy, never forwarded
    core.steward._running[pid] = Running(pid, "command", _closed_port(), "tok", time.time())
    try:
        assert _get(app, pid, "/" + icon).data == want
    finally:
        core.steward._running.pop(pid, None)


def test_only_declared_and_safe_pictures_are_served(app, state):
    _add(app, state, "alarmflow", "stopped")
    folder = state / "packages" / "alarmflow"
    assert _get(app, "alarmflow", "/server.py").status_code == 503          # not a picture: never served
    (folder / "icons" / "alarm.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>',
                                                encoding="utf-8")
    r = _get(app, "alarmflow", "/icons/alarm.svg")
    assert r.status_code == 503 and r.is_json                              # refused by the store's own check


# -- 3. installs.json on Windows -------------------------------------------------------------------------
def test_installs_write_waits_while_windows_refuses_the_replace(tmp_path, monkeypatch):
    inst = Installs(tmp_path)
    inst.set("alarmflow", tmp_path / "a", False, "a")
    real, calls = os.replace, {"n": 0}

    def busy(src, dst):
        calls["n"] += 1
        if calls["n"] < 4:
            raise PermissionError(13, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(installs_mod.os, "replace", busy)
    monkeypatch.setattr(installs_mod, "_WAIT_S", 0)
    inst.set("example-page", tmp_path / "e", False, "e")
    assert set(inst.all()) == {"alarmflow", "example-page"} and calls["n"] == 4


def test_installs_write_never_starts_from_an_empty_read(tmp_path, monkeypatch):
    inst = Installs(tmp_path)
    inst.set("alarmflow", tmp_path / "a", False, "a")
    monkeypatch.setattr(installs_mod, "_WAIT_S", 0)
    monkeypatch.setattr(type(inst.path), "read_text", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "busy")))
    with pytest.raises(PermissionError):
        inst.set("trivia", tmp_path / "t", False, "t")
    monkeypatch.undo()
    assert set(inst.all()) == {"alarmflow"}                     # nothing dropped


def test_an_install_whose_record_cant_be_written_leaves_no_folder(core, monkeypatch):
    from conftest import yes

    def refused(*_a, **_k):
        raise PermissionError(13, "Access is denied")
    staged = core.packages.add("example-page")
    monkeypatch.setattr(core.packages.installs, "set", refused)
    got = yes(core, staged, timeout_s=60)
    assert got["status"] == "failed", got
    assert "could not be written" in " ".join(got["problems"])
    assert not (core.packages.packages / "example-page").exists()          # no half install
    assert core.registry.get("example-page") is None
    monkeypatch.undo()
    assert yes(core, core.packages.add("example-page"), timeout_s=60)["status"] == "installed"   # it can be added again
