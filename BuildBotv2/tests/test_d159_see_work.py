"""D159: she can see what is building and what is indexing, in the window and on the dashboard. The words are checked in
tests/js/logic.test.js; here the route, what a real scan says while it runs and after, and that both pages draw it."""

from __future__ import annotations

import sqlite3

from conftest import REPO
from puffpai_core.zone1 import folders
from puffpai_core.zone1.folders import Library, index_view

WEB = REPO / "packages" / "desktop" / "web"


class _Archivist:
    def __init__(self, state):
        self.state = state


def _library(state, monkeypatch, seen=None):
    """A real Library with its files listed and read by stand-ins, so no vault is needed."""
    lib = Library(_Archivist(state), state)
    names = [state / f"f{n}.txt" for n in range(3)]
    monkeypatch.setattr(lib, "_files", lambda *_app_folders: names)      # WritersBot: _files also lists app folders

    def scan_file(p):
        if seen is not None:
            seen.append(index_view(state))
        return {"status": "indexed" if p.name != "f2.txt" else "unchanged", "held": 0, "reason": ""}
    monkeypatch.setattr(lib, "scan_file", scan_file)
    return lib


def test_a_bot_that_never_indexed_says_so_and_makes_nothing(tmp_path):
    view = index_view(tmp_path)
    assert view["running"] is False and view["files"] == view["pieces"] == view["last_finished"] == 0
    assert list(tmp_path.iterdir()) == []


def test_it_says_how_far_along_while_it_runs_then_when_it_finished(state, monkeypatch):
    seen = []
    lib = _library(state, monkeypatch, seen)
    assert index_view(state)["running"] is False
    report = lib.scan()
    assert report["indexed"] == 2 and report["unchanged"] == 1
    assert [(v["running"], v["done"], v["of"], v["now"]) for v in seen] == [(True, 0, 3, "f0.txt"), (True, 1, 3, "f1.txt"), (True, 2, 3, "f2.txt")]
    after = index_view(state)
    assert after["running"] is False and after["last_finished"] > 0 and after["last_new"] == 2


def test_a_scan_that_fails_does_not_stay_running_or_count_as_finished(state, monkeypatch):
    lib = _library(state, monkeypatch)

    def boom(_p):
        raise RuntimeError("no")
    monkeypatch.setattr(lib, "scan_file", boom)
    try:
        lib.scan()
    except RuntimeError:
        pass
    view = index_view(state)
    assert view["running"] is False and view["last_finished"] == 0


def test_it_counts_what_can_be_searched_in_files_and_pieces(state, monkeypatch):
    lib = _library(state, monkeypatch)
    with sqlite3.connect(lib.db_path) as db:
        db.execute("INSERT INTO files (path, status) VALUES ('a', 'indexed'), ('b', 'indexed'), ('c', 'not_indexed')")
        db.executemany("INSERT INTO chunks (path, n, text) VALUES (?, ?, ?)", [("a", 0, "x"), ("a", 1, "y"), ("b", 0, "z")])
    view = index_view(state)
    assert (view["files"], view["pieces"], view["not_read"]) == (2, 3, 1)


def test_a_bot_in_another_folder_is_not_mixed_up(state, tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()
    folders._SCANS[str(other)] = {"done": 1, "of": 2, "now": "x", "started_at": 1}
    try:
        assert index_view(other)["running"] is True and index_view(state)["running"] is False
    finally:
        folders._SCANS.pop(str(other))


def test_the_route_is_for_her_devices_only_and_says_it_all(core):
    client = core.app.test_client()
    assert client.get("/v1/knowledge/index").status_code == 401
    package = {"Authorization": f"Bearer {core.tokens.issue_package_token('x')}"}
    assert client.get("/v1/knowledge/index", headers=package).status_code == 403
    r = client.get("/v1/knowledge/index", headers={"Authorization": f"Bearer {core.tokens.issue_device('laptop')}"})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert {"running", "done", "of", "now", "files", "pieces", "not_read", "last_finished", "downloading", "learning"} <= set(r.get_json())


def test_both_pages_draw_it_first_as_text():
    window = (WEB / "window.js").read_text(encoding="utf-8")
    dashboard = (WEB / "pages" / "building.js").read_text(encoding="utf-8")
    knowledge = (WEB / "pages" / "knowledge.js").read_text(encoding="utf-8")
    assert "L.workNow(views, r.data, Date.now())" in window and "L.workNow(views, r.data, Date.now())" in dashboard
    assert window.index('"data-work-now"') < window.index("...parts);", window.index('"data-work-now"'))
    assert "[indexTile(), search]" in knowledge and "L.indexNow(r.data, Date.now())" in knowledge
    assert "t.node.isConnected" in knowledge                  # it stops asking when she leaves the page
    assert "/v1/knowledge/index" in (REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8")


# -- D162: each job has one home, named in her words -----------------------------------------------------------------------
def test_each_job_has_its_one_home():
    read = lambda name: (WEB / name).read_text(encoding="utf-8")
    assert 'P.go("apps", "available") } }, [P.icon("add_circle"), "Add an app"]' in read("pages/home.js")
    assert '["add", "Ask Toolsmith"]' in read("pages/apps.js")
    settings = read("pages/settings.js")
    assert '"data-add-model": o.id' in settings and "await P.toolsmithTile()" in settings and "Pin these" not in settings
    assert 'label: "The Bridge"' in read("logic.js") and 'pageItem("outside", "The Bridge")' in read("window.js")
    assert 'title: "The Bridge", draw' in read("pages/outside.js") and 'title: "What left your computer"' in read("pages/outside.js")
    developer = read("pages/developer.js")
    assert 'title: "What left your computer"' in developer and 'href="#outside"' not in developer
    assert "What went out" not in developer and "your pinned documents" not in developer
    window = read("window.js")
    assert 'openDashboard("#vault")' not in window and window.count('btn("Set up the vault", () => openPage("vault"))') == 2


# -- a real browser, real clicks: the dashboard, then the window on its own --------------------------------------------------
def test_both_say_it_in_a_real_browser(state, tmp_path, monkeypatch, drawbridge):
    import pytest
    test_i10a = pytest.importorskip("test_i10a_desktop_clicks")
    if test_i10a.EDGE is None or __import__("os").name != "nt":
        pytest.skip("Windows Job Objects and Microsoft Edge")
    with test_i10a._desktop(state, tmp_path, monkeypatch) as b:
        test_i10a._rail(b, "knowledge")
        assert b.wait("!!document.querySelector('[data-index-now=idle]')", 30)
        said = b.js("document.querySelector('[data-index]').innerText")
        assert "Not indexing right now." in said and "New files in your folders are read the next time you search" in said
        test_i10a._rail(b, "building")
        assert b.wait("!!document.querySelector('[data-work-now=idle]')", 30)
        assert "Nothing is running right now." in b.js("document.querySelector('[data-work-now]').innerText")
        b.js("location.href = '/window/'")
        assert b.wait("document.getElementById('win') && !document.getElementById('win').hidden", 40)
        assert b.wait("!!document.querySelector('[data-rail=knowledge]')", 30)
        box = b.js("(() => { const r = document.querySelector('[data-rail=knowledge]').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
        b.click(*box)
        assert b.wait("!!document.querySelector('[data-index-now=idle]')", 30)
        assert "Not indexing right now." in b.js("document.querySelector('[data-index]').innerText")
        b.js("(() => { document.getElementById('input').value = '/watch'; document.getElementById('composer').requestSubmit(); })()")
        assert b.wait("!!document.querySelector('[data-work-now=idle]')", 30)
        assert "Nothing is running right now." in b.js("document.querySelector('[data-work-now]').innerText")


def test_each_home_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    import pytest
    test_i10a = pytest.importorskip("test_i10a_desktop_clicks")
    if test_i10a.EDGE is None or __import__("os").name != "nt":
        pytest.skip("Windows Job Objects and Microsoft Edge")

    def press(b, sel, text):
        b.click(*test_i10a._text_point(b, sel, text))
    with test_i10a._desktop(state, tmp_path, monkeypatch) as b:
        # the dashboard: Home's Add an app lands on the store; the other tab is Ask Toolsmith
        b.js("location.hash = '#home'")
        assert b.wait("!!document.querySelector('.hello button')", 30)
        press(b, ".hello button", "Add an app")
        assert b.wait("location.hash === '#apps/available'", 20), b.js("location.hash")
        assert b.wait("Array.from(document.querySelectorAll('#view-apps .tabs button')).map((x) => x.textContent).join(',') === 'Installed,PuffPAI store,Ask Toolsmith'", 20)
        # the menu says The Bridge, and it opens its page with one name for its log
        assert b.wait("Array.from(document.querySelectorAll(\".rail a.item[href='#outside']\")).some((x) => x.textContent.indexOf('The Bridge') >= 0)", 20)
        test_i10a._rail(b, "outside")
        assert b.wait("document.querySelector('#view-outside').innerText.indexOf('What left your computer') >= 0", 20)
        # Settings: the cloud model's form is on its row, and an old address lands there
        b.js("location.hash = '#developer/toolsmith'")
        assert b.wait("location.hash === '#settings/toolsmith' && !!document.querySelector(\"#view-settings [data-tile=toolsmith] select[aria-label='Where Toolsmith thinks']\")", 30), b.js("location.hash")
        # the window, on its own: the rail says The Bridge, the vault's button stays in the window, and the cloud model is in Settings
        b.js("location.href = '/window/'")
        assert b.wait("document.getElementById('win') && !document.getElementById('win').hidden", 40)
        assert b.wait("!!document.querySelector('[data-rail=outside]') && document.querySelector('[data-rail=outside]').textContent.indexOf('The Bridge') >= 0", 30)
        assert b.wait("document.body.innerText.indexOf('not set up yet') >= 0", 20)
        b.js("document.querySelector('[data-status=vault]').click()")
        assert b.wait("!!document.querySelector('[data-tabview=\"page:vault\"]')", 20)
        assert b.js("location.pathname") == "/window/"


def test_settings_add_asks_the_same_yes_card_for_a_model_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    import json

    import pytest
    test_i10a = pytest.importorskip("test_i10a_desktop_clicks")
    if test_i10a.EDGE is None or __import__("os").name != "nt":
        pytest.skip("Windows Job Objects and Microsoft Edge")
    ollama = test_i10a.FakeOllamaServer(["qwen3.5:9b"])
    try:
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "model.json").write_text(json.dumps({"address": ollama.address, "model": "qwen3.5:9b"}), encoding="utf-8")
        (state / "settings" / "hardware.json").write_text(json.dumps(
            {"hardware_class": "desktop", "memory": {"total_gb": 16}, "disk": {"free_gb": 200}}), encoding="utf-8")
        with test_i10a._desktop(state, tmp_path, monkeypatch, complete=True) as b:
            b.js("location.hash = '#settings/brain'")
            add = "#view-settings [data-add-model^='qwen2.5']"
            assert b.wait(f"!!document.querySelector({json.dumps(add)})", 30), b.js("document.getElementById('view-settings').innerText")
            b.js("document.querySelectorAll('#view-settings [data-tile=brain], #view-settings [data-tile=brain] details').forEach((d) => { d.open = true; })")
            b.click(*test_i10a._point(b, add, 0))
            assert b.wait("!!document.querySelector('#view-settings [data-tile=brain] .card.yes')", 30), b.js("document.querySelector('#view-settings [data-tile=brain]').innerText")
            assert "Download " in b.js("document.querySelector('#view-settings [data-tile=brain] .card.yes').innerText")
    finally:
        ollama.close()
