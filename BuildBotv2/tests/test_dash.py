"""Lane dash (D82, PROPOSED): Home shows the installed apps on every look, an app's own page can sit in its
own Home tile, and chat can put any app there (the Core tool arrange_home)."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from conftest import REPO
from puffpai_core import layout
from puffpai_core.registry import row_from_manifest
from puffpai_core.zone2 import concierge as cg, loop
from puffpai_core.zone2.concierge import Concierge
from puffpai_core.zone2.tools import ToolTable, call

NODE = shutil.which("node")
WEB = REPO / "packages" / "desktop" / "web"


def _owner(core):
    return {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}


class Rows:
    """The Registry as the Concierge reads it."""

    def __init__(self, *rows):
        self.rows = list(rows)

    def list(self):
        return self.rows


def _row(pid, name, pages=True, status="running", origin="made"):
    ui = {"pages": [{"id": "main", "title": name, "path": "/", "show": "in_desktop"}]} if pages else {}
    return {"id": pid, "name": name, "kind": "app", "status": status, "origin": origin, "ui": ui}


def _tool(state, *rows, keep=lambda t: t):
    c = Concierge(registry=Rows(*rows), state=state, keep=keep)
    table = ToolTable(c.core_tools(lambda: table))

    def run(**args):
        got = call(table, "arrange_home", args, {})
        return got["result"] if got["ok"] else got        # the tool's own answer, or the runner's refusal
    return run


# -- the layout file ---------------------------------------------------------------------------------------
def test_your_apps_is_a_core_tile_and_comes_first(state):
    got = layout.get_layout(state)
    assert got["tiles"]["order"][0] == "apps" and layout.CORE_TILES["apps"] == "Your apps"
    assert got["apps"] == {"view": "tiles", "pages": []}


def test_a_layout_saved_before_d82_gets_your_apps_first_once(state):
    path = layout.path_for(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "tiles": {"order": ["brain", "running"], "hidden": [], "pinned": [],
                                                        "names": {}}}), encoding="utf-8")
    assert layout.get_layout(state)["tiles"]["order"] == ["apps", "brain", "running"]
    layout.arrange(state, "hide", "your apps")                     # she hides it: it stays hidden
    got = layout.get_layout(state)
    assert "apps" in got["tiles"]["hidden"] and got["apps"]["view"] == "tiles"


def test_an_app_page_goes_on_home_first_and_comes_off(state):
    on = layout.app_page(state, "worldmonitor", "World Monitor", True)
    assert on["ok"] and on["id"] == "app/worldmonitor" and "on your Home page" in on["line"]
    saved = on["layout"]
    assert saved["apps"]["pages"] == ["worldmonitor"] and saved["tiles"]["order"][0] == "app/worldmonitor"
    assert saved["seen"]["tiles"]["app/worldmonitor"] == "World Monitor"      # chat finds it by name
    pinned = layout.arrange(state, "pin", "world monitor")
    assert pinned["ok"] and pinned["layout"]["tiles"]["pinned"] == ["app/worldmonitor"]
    off = layout.app_page(state, "worldmonitor", "World Monitor", False)
    assert off["ok"] and off["layout"]["apps"]["pages"] == [] and off["layout"]["tiles"]["pinned"] == []
    assert not layout.app_page(state, "../x", "x", True)["ok"]               # only a package id


def test_apps_as_pages_and_off_again_hides_one(state):
    assert layout.apps_view(state, "pages")["layout"]["apps"]["view"] == "pages"
    off = layout.app_page(state, "freshtable", "FreshTable", False)          # every app shows: this one is hidden
    assert "app/freshtable" in off["layout"]["tiles"]["hidden"]
    back = layout.app_page(state, "freshtable", "FreshTable", True)
    assert "app/freshtable" not in back["layout"]["tiles"]["hidden"]
    assert not layout.apps_view(state, "cards")["ok"]
    broken = {"apps": {"view": "huge", "pages": ["ok-id", "Bad Id", 7, "x"]}}
    assert layout.save_layout(state, broken)["apps"] == {"view": "tiles", "pages": ["ok-id"]}


# -- the route the Desktop calls ---------------------------------------------------------------------------
def test_the_arrange_route_puts_an_installed_app_on_home(core, freshtable):
    client, owner = core.app.test_client(), _owner(core)
    core.registry.add(row_from_manifest(freshtable, "made"))

    def arrange(**body):
        return client.post("/v1/settings/layout/arrange", headers=owner, json=body)

    r = arrange(action="add_app", app="freshtable")
    assert r.status_code == 200 and r.json["id"] == "app/freshtable"
    assert client.get("/v1/settings/layout", headers=owner).json["apps"]["pages"] == ["freshtable"]
    assert arrange(action="add_app", app="nothing-here").status_code == 400
    assert arrange(action="apps_as", view="pages").json["layout"]["apps"]["view"] == "pages"
    assert arrange(action="apps_as", view="cards").status_code == 400
    r = arrange(action="remove_app", app="freshtable")
    assert r.status_code == 200 and r.json["layout"]["apps"]["pages"] == []
    assert client.post("/v1/settings/layout/arrange", json={"action": "add_app", "app": "freshtable"}).status_code == 401
    noui = dict(row_from_manifest(freshtable, "made"), id="noui", name="NoUI", ui={})
    core.registry.add(noui)
    r = arrange(action="add_app", app="noui")
    assert r.status_code == 400 and "no page" in r.json["line"]


# -- chat: arrange_home --------------------------------------------------------------------------------------
def test_arrange_home_is_a_core_tool_and_the_brain_is_told_when_to_use_it():
    assert "arrange_home" in cg.CORE_TOOL_NAMES
    assert "arrange_home, not ask_toolsmith" in loop.TOOL_NOTE


def test_chat_puts_an_app_on_home_by_its_name(state):
    tool = _tool(state, _row("worldmonitor", "World Monitor"))
    got = tool(action="add_app", tile="world monitor")
    assert got["ok"] and "World Monitor is on your Home page now" in json.dumps(got)
    saved = layout.get_layout(state)
    assert saved["apps"]["pages"] == ["worldmonitor"] and saved["updated_by"] == "brain"
    assert tool(action="remove_app", tile="World Monitor")["ok"]
    assert layout.get_layout(state)["apps"]["pages"] == []


def test_chat_pin_on_an_app_not_on_home_puts_it_there_first(state):
    tool = _tool(state, _row("worldmonitor", "World Monitor"))
    got = tool(action="pin", tile="World Monitor")
    assert got["ok"]
    saved = layout.get_layout(state)
    assert saved["apps"]["pages"] == ["worldmonitor"] and saved["tiles"]["pinned"] == ["app/worldmonitor"]


def test_chat_acts_on_the_app_she_named_never_a_core_tile_with_a_word_in_common(state):
    tool = _tool(state, _row("brain-trainer", "Brain Trainer"))
    got = tool(action="pin", tile="Brain Trainer")
    saved = layout.get_layout(state)
    assert got["ok"] and saved["tiles"]["pinned"] == ["app/brain-trainer"] and saved["apps"]["pages"] == ["brain-trainer"]
    assert "Brain Trainer" in got["said"] and "The brain" not in got["said"]
    assert tool(action="pin", tile="the brain")["ok"] and layout.get_layout(state)["tiles"]["pinned"][0] == "brain"


def test_chat_by_app_id_and_core_words_each_find_their_own_tile(state):
    tool = _tool(state, _row("brain-trainer", "Brain Trainer"), _row("bridge", "The Bridge"))
    got = tool(action="pin", tile="app/brain-trainer")
    assert got["ok"] and layout.get_layout(state)["tiles"]["pinned"] == ["app/brain-trainer"]
    assert tool(action="hide", tile="the bridge")["ok"]               # her exact words for a core tile: that tile
    assert "bridge" in layout.get_layout(state)["tiles"]["hidden"]
    assert tool(action="add_app", tile="The Bridge")["ok"]            # add_app always means an app
    assert "bridge" in layout.get_layout(state)["apps"]["pages"]


def test_an_outside_app_is_never_said_to_show_its_page(state):
    tool = _tool(state, _row("worldmonitor", "World Monitor", origin="adopted:https://github.com/x/y"))
    got = tool(action="add_app", tile="World Monitor")
    assert got["ok"] and "new tab" in got["said"] and "showing its own page" not in got["said"]
    assert layout.get_layout(state)["apps"]["pages"] == ["worldmonitor"]


def test_a_bad_move_from_chat_changes_nothing(state):
    tool = _tool(state, _row("worldmonitor", "World Monitor"))
    got = tool(action="move", tile="World Monitor", position="soon")
    assert not got["ok"] and "Nothing changed" in got["said"]
    assert layout.get_layout(state)["apps"]["pages"] == []
    assert not tool(action="hide", tile="World Monitor")["ok"]       # not on Home: says so, adds nothing
    assert layout.get_layout(state)["apps"]["pages"] == []
    moved = tool(action="move", tile="World Monitor", position="last")
    assert moved["ok"] and layout.get_layout(state)["tiles"]["order"][-1] == "app/worldmonitor"


def test_chat_says_plainly_what_it_could_not_do(state):
    tool = _tool(state, _row("notes-cli", "Notes CLI", pages=False))
    no_page = tool(action="add_app", tile="notes cli")
    assert "has no page to show on Home" in json.dumps(no_page) and layout.get_layout(state)["apps"]["pages"] == []
    assert "no app called" in json.dumps(tool(action="add_app", tile="weather"))
    assert tool(action="apps_as_pages")["ok"] and layout.get_layout(state)["apps"]["view"] == "pages"
    assert tool(action="hide", tile="the bridge")["ok"] and "bridge" in layout.get_layout(state)["tiles"]["hidden"]
    assert not tool(action="explode")["ok"]                         # the tool's own enum refuses it


def test_a_rename_from_chat_is_kept_in_its_locked_form(state):
    tool = _tool(state, keep=lambda t: t.replace("secret", "{{vault:abc}}"))
    assert tool(action="rename", tile="the brain", name="my secret brain")["ok"]
    assert layout.get_layout(state)["tiles"]["names"]["brain"] == "my {{vault:abc}} brain"


# -- the Desktop's own logic (node) ----------------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_desktop_side():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "dash.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stdout + got.stderr
    for name in ("logic.js", "pages/home.js", "pages/apps.js"):
        chk = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert chk.returncode == 0, name + chk.stderr


def test_the_new_words_follow_the_writing_rules():
    for path in [WEB / "pages" / "home.js", WEB / "pages" / "apps.js", WEB / "logic.js", WEB / "desktop.css",
                 REPO / "core" / "puffpai_core" / "layout.py", REPO / "core" / "puffpai_core" / "zone2" / "concierge.py",
                 REPO / "tests" / "js" / "dash.test.js"]:
        text = path.read_text(encoding="utf-8")
        assert chr(0x2014) not in text and "beta" not in text.lower().replace("alphabet", ""), path
