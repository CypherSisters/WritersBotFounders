"""D82, D83: GET /v1/packages/<id>/frame. Only a page built for PuffPAI shows inside a Home tile, from its
own origin (D53). An outside app is never framed: it opens in her browser, in a new tab, and the answer
carries only its address for the Open button (on this computer, never a Core port). Nothing is fetched.
Tests never bind 8420-8422: the Core here has ports the OS picked.
"""

import sys
import socket

import pytest

from puffpai_core.api import create_app
from puffpai_core.api.app import core_csp
from puffpai_core.zone2 import apps


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def app(state):
    a = create_app(state, port=_free_port(), pages_port=_free_port())      # never 8420 or 8422
    a.extensions["puffpai"].proxy_port = _free_port()                       # never 8421
    return a


@pytest.fixture
def core(app):
    return app.extensions["puffpai"]


@pytest.fixture
def ask(app, core, monkeypatch):
    """GET /frame as the owner; nothing may be fetched while answering."""
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("the frame answer fetched something"))
    monkeypatch.setattr(urllib.request.OpenerDirector, "open",
                        lambda *a, **k: pytest.fail("the frame answer fetched something"))
    owner = {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}
    client = app.test_client()

    def _ask(id):
        r = client.get(f"/v1/packages/{id}/frame", headers=owner)
        return r.status_code, r.get_json()
    return _ask


def add(core, id, ui, origin="adopted:local", status="running", port=None, name="Notes", kind="app", **extra):
    core.registry.add({"id": id, "kind": kind, "name": name, "origin": origin, "status": status,
                       "ui": ui, "port": port, **extra})


def _store(source="official"):
    return {"source": source, "repo": "https://github.com/CypherSisters/puffpai-store", "path": "notes", "commit": "a" * 40}


#: PuffPAI's own origin: from the official PuffPAI store.
OWN = {"origin": "store:verified", "store": _store()}


KEYS = {"ok", "url", "kind", "sandbox", "running", "line"}


def _shape(body):
    assert set(body) == KEYS, body
    assert isinstance(body["ok"], bool) and isinstance(body["running"], bool)
    assert body["kind"] in ("page", "app", "none")
    assert "\u2014" not in body["line"]


# -- kind page -------------------------------------------------------------------------------------
def test_a_page_built_for_puffpai_shows_from_its_own_origin(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "path": "/board"}]}, status="stopped", **OWN)
    status, body = ask("notes")
    _shape(body)
    assert status == 200 and body["kind"] == "page" and body["ok"] is True and body["running"] is False
    assert body["url"] == f"http://notes.localhost:{core.pages_port}/board"
    assert body["sandbox"] == apps.PAGE_SANDBOX == "allow-scripts allow-forms allow-popups allow-same-origin"


def test_a_built_page_with_a_file_uses_the_file(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "file": "index.html"}]}, **OWN)
    assert ask("notes")[1]["url"] == f"http://notes.localhost:{core.pages_port}/index.html"


# -- kind app: never framed (D83) -------------------------------------------------------------------
def _never_framed(body, url, running=True):
    _shape(body)
    assert body["ok"] is False and body["kind"] == "app" and body["sandbox"] == ""
    assert body["url"] == url and body["running"] is running
    assert body["line"] == "Notes opens in your browser, in a new tab."


def test_an_outside_app_is_never_framed(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "path": "/"}]}, port=18555)
    _never_framed(ask("notes")[1], "http://127.0.0.1:18555/")


def test_a_stopped_outside_app_keeps_its_address(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "path": "/"}]}, port=18555, status="stopped")
    _never_framed(ask("notes")[1], "http://127.0.0.1:18555/", running=False)


def test_a_stopped_app_with_no_port_yet_has_no_address(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "url": "http://127.0.0.1:{port}/"}]}, status="stopped")
    _never_framed(ask("notes")[1], "", running=False)


def test_a_built_page_that_wants_a_new_tab_is_kind_app(core, ask):
    add(core, "notes", {"pages": [{"id": "main", "path": "/", "show": "new_tab"}]}, port=18555, **OWN)
    _never_framed(ask("notes")[1], "http://127.0.0.1:18555/")


@pytest.mark.parametrize("url, want", [
    ("http://me:secret@127.0.0.1:{port}/home", "http://127.0.0.1:18555/home"),      # the sign-in is removed
    ("http://localhost:18556/x", "http://localhost:18556/x"),
    ("http://[::1]:18557/", "http://[::1]:18557/"),
    ("https://127.0.0.1:18558/", "https://127.0.0.1:18558/"),
])
def test_an_address_on_this_computer_is_kept_for_the_open_button(core, ask, url, want):
    add(core, "notes", {"pages": [{"id": "main", "url": url}]}, port=18555)
    body = ask("notes")[1]
    _never_framed(body, want)
    assert "secret" not in str(body)


@pytest.mark.parametrize("url", ["http://example.com/", "http://192.168.1.5:8080/", "http://127.0.0.1.nip.io:9/",
                                 "https://notes.example/", "file:///C:/Windows/win.ini", "ftp://127.0.0.1:21/",
                                 "javascript:alert(1)", "http://0.0.0.0:18500/", "http://127.0.0.1:99999/"])
def test_an_address_off_this_computer_is_not_given(core, ask, url):
    add(core, "notes", {"pages": [{"id": "main", "url": url}]})
    _never_framed(ask("notes")[1], "")


@pytest.mark.parametrize("which", ["port", "pages_port", "proxy_port"])
def test_a_core_port_is_not_given(core, ask, which):
    add(core, "notes", {"pages": [{"id": "main", "url": f"http://127.0.0.1:{getattr(core, which)}/"}]})
    _never_framed(ask("notes")[1], "")


@pytest.mark.parametrize("port", [8420, 8421, 8422])
def test_the_fixed_core_ports_are_not_given_whatever_the_core_runs_on(core, ask, port):
    add(core, "notes", {"pages": [{"id": "main", "url": f"http://localhost:{port}/"}]})
    _never_framed(ask("notes")[1], "")


def test_the_real_proxy_port_is_not_given(core, ask):
    """A proxy started on any free port (proxy_port 0) is still a Core port."""
    real = _free_port()
    core.proxy_port = 0
    core.zone3 = type("Z", (), {"proxy": type("P", (), {"port": real})()})()
    add(core, "notes", {"pages": [{"id": "main", "url": f"http://127.0.0.1:{real}/x"}]})
    _never_framed(ask("notes")[1], "")


# -- kind none, 404, never 500 ---------------------------------------------------------------------
def test_a_program_only_package_has_no_page(core, ask):
    add(core, "editor", {"open": {"program": "editor.exe"}}, name="Editor")
    status, body = ask("editor")
    _shape(body)
    assert status == 200 and body == {"ok": False, "url": "", "kind": "none", "sandbox": "", "running": True,
                                      "line": "Editor has no page to show here."}


def test_no_ui_at_all_has_no_page(core, ask):
    add(core, "quiet", {}, name="Quiet", port=18555)
    assert ask("quiet")[1]["kind"] == "none"


def test_unknown_id_is_404(ask):
    status, body = ask("nothing-here")
    assert status == 404 and "Nothing installed" in body["error"]


def test_owner_only(app, core):
    add(core, "notes", {"pages": [{"id": "main", "path": "/"}]}, origin="local:notes")
    client = app.test_client()
    assert client.get("/v1/packages/notes/frame").status_code == 401
    pkg = {"Authorization": f"Bearer {core.tokens.issue_package_token('notes')}"}
    assert client.get("/v1/packages/notes/frame", headers=pkg).status_code == 403


def test_never_a_500(core, ask, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("broken")
    monkeypatch.setattr(apps, "frame_view", boom)
    add(core, "notes", {"pages": [{"id": "main", "path": "/"}]})
    status, body = ask("notes")
    _shape(body)
    assert status == 200 and body["ok"] is False and body["url"] == ""


# -- the Desktop's own policy stays as it is on main -----------------------------------------------
def test_core_csp_is_as_before():
    """Nothing about outside apps in the Desktop's policy (D83): frames from package pages only."""
    assert core_csp("http://127.0.0.1:8422") == (
        "default-src 'self'; img-src 'self' http://127.0.0.1:8422 http://*.localhost:8422 data: blob:; "
        "frame-src http://*.localhost:8422; connect-src 'self' http://127.0.0.1:8422 http://*.localhost:8422; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def test_the_desktop_frames_package_pages_only(app):
    r = app.test_client().get("/", base_url=app.config["PUFFPAI_DESKTOP_ORIGIN"])   # D79: the Desktop's own name
    csp = {d.split()[0]: d.split()[1:] for d in (x.strip() for x in r.headers["Content-Security-Policy"].split(";")) if d}
    pages_port = app.extensions["puffpai"].pages_port
    assert csp["frame-src"] == [f"http://*.localhost:{pages_port}"] and csp["frame-ancestors"] == ["'none'"]
    assert r.headers["X-Frame-Options"] == "DENY"


# -- the rule on origin (the owner, 10-30): only PuffPAI's own is framed, an allowlist -------------------------------
def _third_party_origins():
    # No local:... here: a folder she added herself is her own now (see test_a_folder_she_added_is_her_own).
    return ["adopted:https://github.com/x/y", "adopted:local", "github:https://github.com/x/y",
            "store:community", "store:verified", "core:packages", "CORE", "core ", "store:other", "", None, 5, ["core"],
            "made:x", "made:", "Made", "MADE", "made ", " made", "made\n", "made\u200b", "made-adopted:x", "adopted:made",
            "github:made", "unmade", ["made"], {"made": True}]


@pytest.mark.parametrize("origin", _third_party_origins())
def test_any_other_origin_is_third_party(origin):
    """store:verified and store:community count only with the row's own record of the official store."""
    assert apps.puffpai_own({"id": "notes", "origin": origin}) is False


@pytest.mark.parametrize("row", [None, 5, "core", [], {}, {"origin": "store:verified", "store": "official"},
                                 {"origin": "store:community", "store": _store("https://example.test/index.json")},
                                 {"origin": "store:verified", "store": _store("C:/my/store")},
                                 {"origin": "store:verified", "store": {"source": None}},
                                 {"origin": "adopted:x", "store": _store()}])      # local:x moved to the own side
def test_rows_that_are_not_puffpai_own(row):
    assert apps.puffpai_own(row) is False


@pytest.mark.parametrize("origin", ["store:verified", "store:community", "core", "made"])
def test_puffpai_own_origins(origin):
    assert apps.puffpai_own({"id": "notes", "origin": origin, "store": _store()}) is True


@pytest.mark.parametrize("origin", ["local:C:/somewhere/notes", "local:made", "local:x", "local:C:/mine/notes"])
def test_a_folder_she_added_is_her_own(origin):
    """The owner's decision for this bot: a folder she added herself, with her Yes, is her own."""
    assert apps.puffpai_own({"id": "notes", "origin": origin}) is True
    assert apps.puffpai_own({"id": "notes", "origin": origin, "store": _store()}) is True


def test_an_app_her_bot_made_frames_just_as_a_store_app_does(core, ask, app):
    """The owner, 10-30: "Apps my own bot builds should open in the side pane, like PuffPAI Store apps." """
    add(core, "made-one", {"pages": [{"id": "main", "path": "/board"}]}, origin="made")
    add(core, "store-one", {"pages": [{"id": "main", "path": "/board"}]}, **OWN)
    made, store = ask("made-one")[1], ask("store-one")[1]
    _shape(made)
    assert made["ok"] is True and made["kind"] == "page" and made["url"] == f"http://made-one.localhost:{core.pages_port}/board"
    assert made["sandbox"] == store["sandbox"] == apps.PAGE_SANDBOX
    client = app.test_client()
    frames = [client.get("/x", environ_base=PAGES, base_url=f"http://{id}.localhost:{core.pages_port}")
              .headers["Content-Security-Policy"].split("frame-ancestors ")[1] for id in ("made-one", "store-one")]
    assert frames[0] == frames[1] == f"http://puffpai.localhost:{core.port}"


@pytest.mark.parametrize("origin", ["made:x", "Made", "made ", " made"])
def test_the_registry_refuses_a_look_alike_of_made(core, origin):
    with pytest.raises(Exception):                     # noqa: B017 - the store's own refusal
        add(core, "notes", {"pages": [{"id": "main", "path": "/"}]}, origin=origin)


@pytest.mark.parametrize("origin", ["made\n", "adopted:made"])
def test_a_look_alike_of_made_that_can_be_stored_is_never_framed(core, ask, origin):
    """The schema's ``$`` lets a trailing newline through; the rule is an exact match, so it does not count."""
    add(core, "notes", {"pages": [{"id": "main", "path": "/"}]}, origin=origin)
    assert ask("notes")[1]["ok"] is False


def test_any_local_folder_is_her_own_built_in_or_not():
    from puffpai_core.packages.manager import builtin_source
    real = builtin_source("example-page")
    assert real is not None
    assert apps.puffpai_own({"id": "example-page", "origin": f"local:{real}"}) is True
    assert apps.puffpai_own({"id": "freshtable", "origin": f"local:{real}"}) is True                # another id's folder
    assert apps.puffpai_own({"id": "example-page", "origin": "local:C:/evil/example-page"}) is True   # the same name
    assert apps.puffpai_own({"id": "../example-page", "origin": f"local:{real}"}) is True
    assert apps.puffpai_own({"id": "no-such-one", "origin": "local:C:/x/no-such-one"}) is True


@pytest.mark.parametrize("origin", ["adopted:https://github.com/x/y", "github:https://github.com/x/y", "store:community"])   # local:... is her own now
def test_a_third_party_page_is_never_framed_and_opens_in_her_browser(core, ask, origin):
    """Its files, on its own address, where no owner login reaches (D49, D53). "store:community" here has no record of
    the official store."""
    add(core, "notes", {"pages": [{"id": "main", "file": "index.html", "show": "in_desktop"}]}, origin=origin,
        **({"store": _store("https://example.test/index.json")} if origin.startswith("store:") else {}))
    status, body = ask("notes")
    _shape(body)
    assert status == 200 and body["ok"] is False and body["kind"] == "app" and body["sandbox"] == ""
    assert body["url"] == f"http://notes.localhost:{core.pages_port}/index.html"


@pytest.mark.parametrize("kind", ["app", "tool", "page", "ui", "avatar", "look", "bundle"])
def test_no_kind_or_manifest_field_reaches_the_frame_path(core, ask, kind):
    add(core, "notes", {"pages": [{"id": "main", "path": "/", "show": "in_desktop", "sandbox": "allow-top-navigation",
                                   "own": True, "origin": "core"}]}, origin="adopted:https://github.com/x/y", kind=kind,
        description="own: true", version="1.0.0")
    _status, body = ask("notes")
    assert body["ok"] is False and body["kind"] == "app"


def test_the_registry_list_says_own_and_a_stored_value_cant_change_it(app, core):
    add(core, "theirs", {"pages": [{"id": "main", "path": "/"}]}, origin="adopted:https://github.com/x/theirs")
    add(core, "ours", {"pages": [{"id": "main", "path": "/"}]}, **OWN)
    owner = {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}
    rows = {r["id"]: r for r in app.test_client().get("/v1/registry", headers=owner).get_json()}
    assert rows["ours"]["own"] is True and rows["theirs"]["own"] is False
    assert "own" not in core.registry.get("ours")                    # never stored


@pytest.mark.parametrize("claim", ["core", "made", "store:verified"])
def test_a_real_add_can_not_claim_an_origin(app, core, tmp_path, monkeypatch, drawbridge, claim):
    """A real ``puffpai add`` of a folder whose manifest claims another origin: the row gets the folder's origin, not the claim."""
    import json
    from puffpai_core.cli import main
    folder = tmp_path / "claims"
    folder.mkdir()
    (folder / "index.html").write_text("<p>hello</p>", encoding="utf-8")
    (folder / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": "claims", "kind": "page", "name": "Claims", "version": "1.0.0",
        "description": "Says it is the Core.", "author": "x", "license": "MIT", "origin": claim,
        "ui": {"pages": [{"id": "main", "title": "M", "file": "index.html", "show": "in_desktop"}]},
        "needs": {"hosts": [], "keys": []}, "consent": "A page."}), encoding="utf-8")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["add", str(folder)]) == 0
    row = core.registry.get("claims")
    assert row["origin"] == f"local:{folder}" and row["origin"] != claim     # the claim did not become the origin
    assert apps.puffpai_own(row) is True                                    # own because she added the folder


PAGES = {"puffpai.listener": "pages"}


def test_the_browser_holds_the_rule_too_a_third_party_page_refuses_every_frame(app, core):
    """frame-ancestors, on the page's own answer: whatever a page's code decides, it can't be framed."""
    add(core, "theirs", {"pages": [{"id": "main", "path": "/"}]}, origin="adopted:https://github.com/x/y")
    add(core, "mine", {"pages": [{"id": "main", "path": "/"}]}, origin="github:https://github.com/x/mine")
    add(core, "ours", {"pages": [{"id": "main", "path": "/"}]}, **OWN)
    client = app.test_client()
    ask = lambda id: client.get("/x", environ_base=PAGES, base_url=f"http://{id}.localhost:{core.pages_port}")   # noqa: E731
    for id in ("theirs", "mine", "not-installed"):
        policy = ask(id).headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in policy and "puffpai.localhost" not in policy, id
    assert f"frame-ancestors http://puffpai.localhost:{core.port}" in ask("ours").headers["Content-Security-Policy"]


def test_the_desktops_frame_src_is_only_package_pages_on_the_pages_port():
    policy = core_csp("http://127.0.0.1:8422")
    frame_src = next(p.strip() for p in policy.split(";") if p.strip().startswith("frame-src"))
    assert frame_src == "frame-src http://*.localhost:8422"          # no 127.0.0.1, no localhost, no other port
    assert "127.0.0.1:*" not in policy and "localhost:*" not in policy


# -- a built-in is the repo's own folder as plain text, through no link -----------------------------------------------
def _junction(link, target):
    import subprocess
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows junctions")
def test_a_junction_does_not_change_that_a_local_folder_is_her_own(tmp_path, monkeypatch):
    """A local folder is her own, link or not. Scratch folders only, nothing is written through a link, and each link is removed by itself."""
    import os
    packages = tmp_path / "packages"
    (packages / "example-page").mkdir(parents=True)
    (packages / "example-page" / "puffpai.json").write_text("{}", encoding="utf-8")
    other = tmp_path / "other" / "example-page"
    other.mkdir(parents=True)
    monkeypatch.setenv("PUFFPAI_BUILTIN", str(packages))
    plain = packages / "example-page"
    assert apps.puffpai_own({"id": "example-page", "origin": f"local:{plain}"}) is True      # the real thing

    swapped = tmp_path / "theirs" / "example-page"                          # the verifier's: a source folder swapped
    swapped.parent.mkdir()
    _junction(swapped, plain)
    above = tmp_path / "above"                                              # a junction higher up the path
    _junction(above, packages)
    try:
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{swapped}"}) is True
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{above / 'example-page'}"}) is True
        monkeypatch.setenv("PUFFPAI_BUILTIN", str(above))                   # D108: the repo reached through a junction
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{above / 'example-page'}"}) is True
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{plain}"}) is True    # the same, resolved
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{swapped}"}) is True
    finally:
        os.rmdir(swapped)
        os.rmdir(above)

    monkeypatch.setenv("PUFFPAI_BUILTIN", str(packages))
    (plain / "puffpai.json").unlink()
    plain.rmdir()
    _junction(plain, other)                                                 # the repo's own folder is the link
    (other / "puffpai.json").write_text("{}", encoding="utf-8")
    try:
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{plain}"}) is True
        assert apps.puffpai_own({"id": "example-page", "origin": f"local:{other}"}) is True  # where it points
    finally:
        os.rmdir(plain)


@pytest.mark.parametrize("stored", ["{repo}\\..\\example-page", "{repo}/", "{repo}\\", "{repo}\\."])
def test_a_local_folder_is_her_own_however_its_path_is_written(stored):
    from puffpai_core.desktop import builtin_dir
    repo = str(builtin_dir() / "example-page")
    assert apps.puffpai_own({"id": "example-page", "origin": "local:" + stored.format(repo=repo)}) is True


# -- D108: a repo under OneDrive or a junction ---------------------------------------------------------------------------
class _Lstat:
    def __init__(self, tag, mode=0o40777):
        self.st_reparse_tag, self.st_mode = tag, mode


@pytest.mark.parametrize("tag, own", [(0, True),
                                      (0x9000601A, True),         # OneDrive's Documents, read from this computer
                                      (0x9000001A, True), (0x9000701A, True),
                                      (0xA000000C, True),         # a symlink
                                      (0xA0000003, True),         # a junction
                                      (0xA000001D, True)])        # a WSL symlink
def test_a_local_folder_is_her_own_whatever_link_tag_it_has(monkeypatch, tag, own):
    """Stubbed tags, on the repo's real example-page. Nothing under OneDrive is touched."""
    from puffpai_core.packages import manager
    from puffpai_core.packages.manager import builtin_source
    real = builtin_source("example-page")
    stock = manager.os.lstat
    monkeypatch.setattr(manager.os, "lstat", lambda p, **k: _Lstat(tag) if str(p).endswith("example-page") else stock(p, **k))
    assert apps.puffpai_own({"id": "example-page", "origin": f"local:{real}"}) is own


def test_a_local_folder_that_cant_be_read_is_still_her_own(monkeypatch):
    from puffpai_core.packages import manager
    from puffpai_core.packages.manager import builtin_source
    real = builtin_source("example-page")
    stock = manager.os.lstat

    def boom(p, **k):
        if str(p).endswith("example-page"):
            raise OSError("no")
        return stock(p, **k)
    monkeypatch.setattr(manager.os, "lstat", boom)
    assert apps.puffpai_own({"id": "example-page", "origin": f"local:{real}"}) is True


def test_a_cloud_tag_above_the_package_does_not_matter(monkeypatch):
    """No folder is read at all: a local origin is her own by its name."""
    from puffpai_core.packages import manager
    from puffpai_core.packages.manager import builtin_source
    real = builtin_source("example-page")
    seen = []
    stock = manager.os.lstat
    def lstat(p, **k):
        # On Linux and Mac realpath itself walks every folder with lstat; only what the Core asks counts.
        if sys._getframe(1).f_globals.get("__name__") != "posixpath":
            seen.append(str(p))
        return stock(p, **k)
    monkeypatch.setattr(manager.os, "lstat", lstat)
    assert apps.puffpai_own({"id": "example-page", "origin": f"local:{real}"}) is True
    assert not seen


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows junctions")
def test_a_swapped_third_party_row_is_not_a_built_in_for_the_package_manager(tmp_path, monkeypatch):
    """manager.builtin_install used to resolve through a link, and would have replaced the row's copy from the repo."""
    import os
    from puffpai_core.packages.manager import builtin_install
    packages = tmp_path / "repo" / "packages"
    (packages / "example-page").mkdir(parents=True)
    (packages / "example-page" / "puffpai.json").write_text("{}", encoding="utf-8")
    installed = tmp_path / "state" / "packages"
    (installed / "example-page").mkdir(parents=True)
    monkeypatch.setenv("PUFFPAI_BUILTIN", str(packages))
    plain = packages / "example-page"

    def row(path):
        return {"origin": f"local:{path}"}, {"source": str(path), "folder": str(installed / "example-page")}
    assert builtin_install("example-page", *row(plain), installed) == plain.resolve()    # the real thing
    swapped = tmp_path / "theirs" / "example-page"
    swapped.parent.mkdir()
    _junction(swapped, plain)
    try:
        assert builtin_install("example-page", *row(swapped), installed) is None
    finally:
        os.rmdir(swapped)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows junctions")
def test_the_built_in_folder_as_a_link_is_no_built_in_at_all(tmp_path, monkeypatch):
    import os
    from puffpai_core.packages.manager import builtin_source
    packages = tmp_path / "packages"
    packages.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "puffpai.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_BUILTIN", str(packages))
    _junction(packages / "example-page", elsewhere)
    try:
        assert builtin_source("example-page") is None
    finally:
        os.rmdir(packages / "example-page")
