"""D141: move a finished bot to another computer: export to a folder for a git repo, import there. What is private never
goes in, and a bundle that changed on the way is refused whole.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core import bot, bot_move, jsonfile, layout, opens
from puffpai_core.api.app import create_app
from puffpai_core.zone1.folders import Library
from puffpai_core.zone2 import persona
from z1kit import make

KEY = "sk-ant-api03-" + "abcDEF123456" * 8
PHONE = "my number is 415-555-0134"


class Installs:
    def __init__(self, folders):
        self.folders = folders

    def folder(self, pid):
        return self.folders.get(pid)


class Packages:
    def __init__(self, builtin=(), folders=None):
        self.builtin, self.installs = set(builtin), Installs(folders or {})

    def builtin_of(self, pid):
        return Path("x") if pid in self.builtin else None


class Registry:
    def __init__(self, rows=()):
        self.rows = {r["id"]: r for r in rows}

    def list(self, kind=None, **_k):
        return [r for r in self.rows.values() if kind is None or r.get("kind") == kind]

    def get(self, pid):
        return self.rows.get(pid)


def _bot(state):
    bot.save(state, name="Editor", by="owner")
    persona.save(state, {"text": "You are a careful editor. You never write her book.", "source": "owner"})
    opens.save(state, "chat")
    jsonfile.write(layout.path_for(state), {"hidden": ["store"]})


def _app_folder(tmp_path, name="made"):
    folder = tmp_path / "src" / name
    folder.mkdir(parents=True)
    (folder / "puffpai.json").write_text('{"id": "%s"}' % name, encoding="utf-8")
    (folder / "server.py").write_text("print('hello')\n", encoding="utf-8")
    (folder / "icon.png").write_bytes(b"\x89PNG\x00\x01")
    (folder / "saved.db").write_bytes(b"what someone typed")
    (folder / ".env").write_text("NOT_A_REAL=thing", encoding="utf-8")
    (folder / "blob.dat").write_bytes(b"\x00\x01\x02" * 10)
    (folder / ".venv").mkdir()
    (folder / ".venv" / "x.py").write_text("x", encoding="utf-8")
    return folder


def _world(tmp_path):
    made = _app_folder(tmp_path)
    reg = Registry([
        {"id": "office", "name": "Office files", "kind": "tool", "version": "1.0.1", "origin": "core", "status": "running"},
        {"id": "weather", "name": "Weather", "kind": "app", "version": "2.0.0", "origin": "store:verified", "status": "running", "store": {"commit": "a" * 40}},
        {"id": "made", "name": "Made thing", "kind": "app", "version": "0.1.0", "origin": "adopted:https://github.com/x/y", "status": "running"},
        {"id": "hub", "name": "Hub", "kind": "tool", "version": "1", "origin": "github:https://github.com/x/hub", "status": "stopped"},
        {"id": "kn-kiwix-gardens", "name": "Gardens", "kind": "knowledge", "origin": "adopted:https://x/y.zim", "status": "stopped"},
        {"id": "desktop", "name": "Desktop", "kind": "ui", "version": "1", "origin": "core", "status": "running"},
    ])
    return reg, Packages(builtin={"office", "desktop"}, folders={"made": made})


def test_export_writes_what_travels_and_nothing_private(state, tmp_path):
    a, *_ = make(state)
    _bot(state)
    mine = tmp_path / "Writing"
    mine.mkdir()
    doc = tmp_path / "secret place" / "My Novel.docx"
    doc.parent.mkdir()
    doc.write_bytes(b"x")
    lib = Library(a, state)
    lib.link(mine)
    (state / "settings").mkdir(exist_ok=True)
    jsonfile.write(state / "settings" / "folders.json", {**jsonfile.read(state / "settings" / "folders.json", {}), "pinned": [str(doc)]})
    # none of this may ever go
    jsonfile.write(state / "settings" / "people.json", {"people": [{"name": "Zebulon Quux"}]})
    jsonfile.write(state / "settings" / "document_edits.json", {"on": True})
    jsonfile.write(state / "settings" / "tool_servers.json", {"servers": [{"id": "s", "name": "My Tools", "url": "http://192.168.1.9:8000/mcp"}]})
    reg, pk = _world(tmp_path)
    out = tmp_path / "editor-bot"
    got = bot_move.export(state, reg, pk, out)
    assert set(got["apps"]) == {"Office files", "Weather", "Made thing", "Hub"} and got["left_out"] == []
    rec = json.loads((out / "puffpai-bot.json").read_text(encoding="utf-8"))
    assert rec["bot"]["name"] == "Editor" and rec["persona"]["text"].startswith("You are a careful")
    assert rec["opens"] == "chat" and rec["layout"] == {"hidden": ["store"]}
    apps = {x["id"]: x for x in rec["apps"]}
    assert apps["office"]["source"] == "office" and apps["office"]["version"] == "1.0.1"
    assert apps["weather"]["source"] == "store:weather" and apps["weather"]["commit"] == "a" * 40
    assert apps["hub"]["source"] == "https://github.com/x/hub" and apps["made"]["source"] == "apps/made"
    assert "desktop" not in apps
    assert rec["knowledge"]["linked"] == ["Writing"] and rec["knowledge"]["pinned"] == ["My Novel.docx"]
    assert rec["knowledge"]["books"] == [{"source": "kn-kiwix-gardens", "title": "Gardens"}]
    assert rec["added"] == ["My Tools"]
    # the app's code, but nothing that can't be scanned or that holds what someone typed
    carried = {p.name for p in (out / "apps" / "made").rglob("*")}
    assert {"puffpai.json", "server.py", "icon.png"} <= carried
    assert not carried & {"saved.db", ".env", "blob.dat", ".venv", "x.py"}
    assert set(apps["made"]["files_left_out"]) == {"saved.db", ".env", "blob.dat"}
    every = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in out.rglob("*") if p.is_file())
    for never in ("secret place", str(tmp_path), "192.168.1.9", "Zebulon", "document_edits", '"on": true'):
        assert never not in every, never
    assert (out / ".gitattributes").read_text(encoding="utf-8") == "* -text\n"          # a checkout keeps the bytes
    assert "What doesn't travel" in (out / "README.md").read_text(encoding="utf-8")


def test_nothing_tied_to_this_machine_goes(state, tmp_path):
    _bot(state)
    settings = state / "settings"
    for name in ("hardware.json", "ports.json", "terminal.json", "wizard.json", "bridge.json", "steward.json"):
        (settings / name).write_text('{"tied_to_this_machine": "marker-xyz"}', encoding="utf-8")
    (settings / "anchor.key").write_bytes(b"marker-xyz")
    (state / "pairing.json").write_text('{"marker": "marker-xyz"}', encoding="utf-8")
    (state / "tokens.db").write_bytes(b"marker-xyz")
    (state / "window").mkdir()
    (state / "window" / "profile").write_text("marker-xyz", encoding="utf-8")
    made = _app_folder(tmp_path)
    (made / ".buildbot").mkdir()
    (made / ".buildbot" / "compose.puffpai.yml").write_text("proxy: credential-marker-xyz", encoding="utf-8")
    (made / ".buildbot" / "puffpai.json").write_text('{"id": "made"}', encoding="utf-8")
    (made / "node_modules").mkdir()
    (made / "node_modules" / "x.js").write_text("marker-xyz", encoding="utf-8")
    reg = Registry([{"id": "made", "name": "Made", "kind": "app", "version": "1", "origin": "adopted:https://github.com/x/y", "status": "running"}])
    out = tmp_path / "out"
    bot_move.export(state, reg, Packages(folders={"made": made}), out)
    for p in out.rglob("*"):
        if p.is_file():
            assert b"marker-xyz" not in p.read_bytes(), p
    assert (out / "apps" / "made" / ".buildbot" / "puffpai.json").is_file()      # her app's own manifest does go
    assert not (out / "apps" / "made" / ".buildbot" / "compose.puffpai.yml").exists()


def test_an_app_proved_only_on_another_kind_of_computer_isnt_added(state, tmp_path):
    _bot(state)
    made = _app_folder(tmp_path)
    (made / "puffpai.json").write_text(json.dumps({"id": "made", "run": {"plan9": {"type": "command", "start": ["x"]}}}), encoding="utf-8")
    fine = _app_folder(tmp_path, "fine")
    (fine / "puffpai.json").write_text(json.dumps({"id": "fine", "run": {"default": {"type": "command", "start": ["x"]}}}), encoding="utf-8")
    reg = Registry([{"id": n, "name": n.title(), "kind": "app", "version": "1", "origin": "adopted:https://github.com/x/y", "status": "running"} for n in ("made", "fine")]
                   + [{"id": "office", "name": "Office", "kind": "tool", "version": "1", "origin": "core", "status": "running"}])
    out = tmp_path / "out"
    bot_move.export(state, reg, Packages(builtin={"office"}, folders={"made": made, "fine": fine}), out)
    record = bot_move.read(out)
    said = bot_move.lines_for(record)
    assert {a["id"] for a in said["apps"]} == {"fine", "office"} and said["not_here"] == ["Made"]
    assert {a["id"] for a in bot_move.to_add(record)} == {"fine", "office"}
    from puffpai_core.wizard.terminal import moved_lines
    assert any("another kind of computer" in x and "Made" in x for x in moved_lines(said))


def test_a_private_detail_in_its_own_record_writes_nothing(state, tmp_path):
    _bot(state)
    persona.save(state, {"text": f"Her key is {KEY}", "source": "owner"})
    out = tmp_path / "out"
    with pytest.raises(bot_move.MoveRefused, match="nothing was written"):
        bot_move.export(state, Registry(), Packages(), out)
    assert not out.exists()
    persona.save(state, {"text": f"Call her, {PHONE}.", "source": "owner"})
    with pytest.raises(bot_move.MoveRefused, match="nothing was written"):
        bot_move.export(state, Registry(), Packages(), out)
    assert not out.exists()


@pytest.mark.parametrize("bad", [KEY, PHONE])
def test_a_private_detail_in_an_apps_code_leaves_that_app_out_and_says_so(state, tmp_path, bad):
    _bot(state)
    made = _app_folder(tmp_path)
    (made / "config.py").write_text(f'SETTING = "{bad}"\n', encoding="utf-8")
    reg = Registry([{"id": "made", "name": "Made thing", "kind": "app", "version": "1", "origin": "adopted:https://github.com/x/y", "status": "running"}])
    out = tmp_path / "out"
    got = bot_move.export(state, reg, Packages(folders={"made": made}), out)
    assert got["apps"] == [] and "config.py" in got["left_out"][0]["why"]
    assert not (out / "apps").exists()
    rec = json.loads((out / "puffpai-bot.json").read_text(encoding="utf-8"))
    assert rec["apps"][0]["from"] == "https://github.com/x/y"
    assert bad not in (out / "puffpai-bot.json").read_text(encoding="utf-8")


def test_where_it_wont_write(state, tmp_path):
    _bot(state)
    for folder in (state, state / "data", state / "vault" / "x", Path("relative")):
        with pytest.raises(bot_move.MoveRefused):
            bot_move.export(state, Registry(), Packages(), folder)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "taxes.xlsx").write_bytes(b"x")
    with pytest.raises(bot_move.MoveRefused, match="other things"):
        bot_move.export(state, Registry(), Packages(), busy)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    bot_move.export(state, Registry(), Packages(), repo)                 # an empty git repo is the point
    bot_move.export(state, Registry(), Packages(), repo)                 # and again, over its own
    assert (repo / ".git").is_dir() and (repo / "puffpai-bot.json").is_file()


def _exported(state, tmp_path):
    _bot(state)
    reg, pk = _world(tmp_path)
    out = tmp_path / "out"
    bot_move.export(state, reg, pk, out)
    return out


def test_a_bundle_that_changed_is_refused_whole(state, tmp_path):
    out = _exported(state, tmp_path)
    assert bot_move.read(out)["bot"]["name"] == "Editor"
    server = out / "apps" / "made" / "server.py"
    good = server.read_bytes()
    server.write_bytes(good + b"import os\n")
    with pytest.raises(bot_move.MoveRefused, match="changed"):
        bot_move.read(out)
    server.write_bytes(good)
    (out / "apps" / "made" / "extra.py").write_text("x", encoding="utf-8")
    with pytest.raises(bot_move.MoveRefused, match="isn't on this bundle's list"):
        bot_move.read(out)
    (out / "apps" / "made" / "extra.py").unlink()
    sums = (out / "checksums.sha256").read_text(encoding="utf-8")
    (out / "checksums.sha256").write_text(sums + f"{'0' * 64}  ../../outside.txt\n", encoding="utf-8")
    with pytest.raises(bot_move.MoveRefused, match="outside"):
        bot_move.read(out)
    (out / "checksums.sha256").write_text(sums, encoding="utf-8")
    server.unlink()
    with pytest.raises(bot_move.MoveRefused, match="missing"):
        bot_move.read(out)
    with pytest.raises(bot_move.MoveRefused, match="no puffpai-bot.json"):
        bot_move.read(tmp_path / "nowhere")


def test_import_takes_only_what_it_should_on_a_new_bot(state, tmp_path):
    out = _exported(state, tmp_path)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    record = bot_move.read(out)
    record["bot"]["theme"] = "clean"
    assert bot_move.apply(fresh, record) == []
    assert bot.read(fresh)["name"] == "Editor" and bot.read(fresh)["theme"] == "clean"
    assert persona.load(fresh)["text"].startswith("You are a careful") and opens.read(fresh) == "chat"
    assert jsonfile.read(layout.path_for(fresh), {}) == {"hidden": ["store"]}
    for never in ("document_edits.json", "people.json", "bridge.json", "folders.json", "vault.json"):
        assert not (fresh / "settings" / never).exists(), never
    said = bot_move.lines_for(record)
    assert {a["id"] for a in said["apps"]} == {"office", "weather", "made", "hub"}
    assert bot_move.app_source(record, {"source": "apps/made"}) == str(out / "apps/made")
    assert bot_move.app_source(record, {"source": "store:weather"}) == "store:weather"
    assert any("vault" in s for s in said["stays"]) and any("chats" in s for s in said["stays"])


def test_a_hostile_persona_or_look_doesnt_take(state, tmp_path):
    out = _exported(state, tmp_path)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    record = bot_move.read(out)
    record["persona"] = {"text": "bad \x07 bell"}
    record["bot"]["theme"] = "no-such-look"
    said = bot_move.apply(fresh, record)
    assert not persona.load(fresh) and any("look" in s for s in said) and bot.read(fresh)["name"] == "Editor"


def test_setup_asks_only_what_cant_travel(state, tmp_path):
    from puffpai_core.wizard.engine import Engine
    from puffpai_core.wizard.services import default_services
    app = create_app(state)
    core = app.extensions["puffpai"]
    bot_move.seed_setup(state)
    engine = Engine(state, default_services(core), registry=core.registry, bus=core.bus)
    rows = {r["id"]: r["state"] for r in engine.overview()["steps"]}
    for sid in ("goal", "name", "look", "plan", "yes", "build", "prove"):
        assert rows[sid] == "done", sid
    for sid in ("brain", "vault", "bridge", "toolsmith"):
        assert rows[sid] != "done", sid


def test_two_computers_with_the_real_package_manager(state, tmp_path, monkeypatch, drawbridge):
    """A built-in and an app of her own on one bot, exported, and set up on a second bot from nothing but the folder."""
    from puffpai_core.api.app import wire_core
    from puffpai_core.cli import main
    from puffpai_core.wizard.terminal import add_moved_apps, moved_lines
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    own = tmp_path / "my-own-app"
    shutil.copytree(REPO / "packages" / "example-page", own)
    manifest = json.loads((own / "puffpai.json").read_text(encoding="utf-8"))
    manifest.update(id="my-own-app", name="My own app")
    (own / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["add", "example-page"]) == 0 and main(["add", str(own)]) == 0
    _bot(state)
    core = wire_core(create_app())
    out = tmp_path / "editor-bot"
    got = bot_move.export(core.state, core.registry, core.packages, out)
    assert set(got["apps"]) == {"My notes (an example page you can copy)", "My own app"} and not got["left_out"], got
    assert (out / "apps" / "my-own-app" / "puffpai.json").is_file() and not (out / "apps" / "example-page").exists()

    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE", str(second))
    core2 = wire_core(create_app())
    assert core2.state == second and core2.registry.get("my-own-app") is None
    core2.packages.start_after_install = False
    record = bot_move.read(out)
    assert bot_move.apply(core2.state, record, core2.registry) == []
    said: list[str] = []
    added = add_moved_apps(core2, record, lambda _prompt: "y", said.append)
    assert set(added) == {"example-page", "my-own-app"}, said
    assert core2.registry.get("my-own-app")["version"] == manifest["version"]
    assert bot.read(second)["name"] == "Editor" and persona.load(second)["text"].startswith("You are a careful")
    # Each app really asked: said No, nothing is added
    third = tmp_path / "third"
    third.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE", str(third))
    core3 = wire_core(create_app())
    core3.packages.start_after_install = False
    assert add_moved_apps(core3, record, lambda _prompt: "n", said.append) == []
    assert core3.registry.get("my-own-app") is None
    lines = moved_lines(bot_move.lines_for(record))
    assert any("vault" in x for x in lines)


def test_the_routes(state, tmp_path):
    _bot(state)
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    package = {"Authorization": "Bearer " + core.tokens.issue_package_token("office")}
    client = app.test_client()
    out = str(tmp_path / "out")
    assert client.get("/v1/settings/move", headers=dev).json["stays"] == list(bot_move.STAYS)
    for url in ("export", "check", "import"):
        full = f"/v1/settings/move/{url}"
        assert client.post(full, json={"path": out}).status_code in (401, 403)
        assert client.post(full, json={"path": out}, headers=owner).status_code == 403
        assert client.post(full, json={"path": out}, headers=package).status_code == 403
        assert client.post(full, json={}, headers=dev).status_code == 400
    assert client.post("/v1/settings/move/check", json={"path": out}, headers=dev).status_code == 400
    got = client.post("/v1/settings/move/export", json={"path": out}, headers=dev)
    assert got.status_code == 200 and got.json["folder"] == out
    assert client.post("/v1/settings/move/check", json={"path": out}, headers=dev).json["name"] == "Editor"
    assert client.post("/v1/settings/move/import", json={"path": out}, headers=dev).json["name"] == "Editor"


def test_no_windows_only_call_in_the_move():
    src = (REPO / "core" / "puffpai_core" / "bot_move.py").read_text(encoding="utf-8")
    assert "startfile" not in src and "winreg" not in src and "powershell" not in src.lower()


def test_settings_draws_with_edits_and_move_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    """The page parses and draws, in real Edge on a real Core, and both tiles work. The merge that broke this parsed nowhere
    but a browser, so this looks in one."""
    from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        b.click(*_point(b, ".rail a.item[href='#settings']", 0))
        assert b.wait("!!document.querySelector('[data-tile=move]') && !!document.querySelector('[data-tile=edits]')", timeout_s=30), b.js(WHERE)
        assert b.wait("document.querySelectorAll('[data-move-stays] li').length >= 5", timeout_s=15), b.js(WHERE)
        assert b.wait("!!document.querySelector('[data-edits]')", timeout_s=15), b.js(WHERE)
        said = b.js("document.querySelector('[data-move-stays]').textContent")
        assert "vault" in said and "chats" in said
        assert b.js("document.querySelector('[data-edits]').checked") is False
        # Move this bot writes a real folder, from the page
        out = str(tmp_path / "moved")
        got = _call(b, "POST", "/v1/settings/move/export", {"path": out})
        assert got["status"] == 200 and (tmp_path / "moved" / "puffpai-bot.json").is_file(), got
        # the window's own Settings has the same setting
        b.js("location.href = '/window/'; true")
        assert b.wait("!!window.Puff && !!window.Puff.editsSetting && !!window.Puff.passageCard", timeout_s=30), b.js(WHERE)
