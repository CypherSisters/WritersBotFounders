"""Lane I8b: the v1 dashboard's pages in the Desktop package, on v2's Core API.

- The Core serves every file the Desktop is made of (``/ui/<path>``), and every file index.html names.
- Every Core route a Desktop page calls is in contracts/openapi.yaml (lane I8c's Developer shell excepted).
- The Home layout routes (harvested from v1 ``dashboard_widgets.py``) and the built-ins list.
- Puff's live face is served from her own origin only (D53), and her preview is v1's drawing.
"""

import json
import re
import shutil

import pytest
import yaml

from conftest import REPO, yes
from puffpai_core.api import create_app
from puffpai_core.registry import row_from_manifest

WEB = REPO / "packages" / "desktop" / "web"
OPENAPI = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
#: Routes another lane adds (lane I8c: the Developer shell; lane I10c: the bot's name and theme). The Desktop
#: falls back while they answer 404 ("Coming in a later build", or the name Puff and its own palettes).
OTHER_LANES = ("/v1/dev/shell", "/v1/settings/bot", "/v1/settings/themes")


@pytest.fixture
def app(state):
    return create_app(state)


@pytest.fixture
def client(app):
    from desktop_kit import desktop_client
    return desktop_client(app)                       # D79: the Desktop is served at puffpai.localhost only


@pytest.fixture
def owner(app):
    return {"Authorization": "Bearer " + app.extensions["puffpai"].tokens.issue_owner_key()}


def _desktop_files():
    return sorted(p.relative_to(WEB).as_posix() for p in WEB.rglob("*") if p.is_file())


def test_the_core_serves_every_desktop_file(client):
    files = _desktop_files()
    for name in ("index.html", "desktop.css", "logic.js", "theme.js", "desktop.js", "pages/home.js", "pages/chat.js",
                 "fonts/fredoka/Fredoka.woff2", "fonts/nunito/Nunito.woff2", "fonts/jetbrains-mono/JetBrainsMono-Bold.woff2",
                 "fonts/material-symbols/material-symbols-rounded.woff2", "img/design-cloud-3.jpg", "img/puff.webp"):
        assert name in files, name
    for name in files:
        r = client.get("/ui/" + name)
        assert r.status_code == 200, name
        assert r.data == (WEB / name).read_bytes(), name


def test_every_file_index_html_names_is_there(client):
    html = (WEB / "index.html").read_text(encoding="utf-8")
    names = re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html)
    assert "/ui/desktop.js" in names and "/ui/logic.js" in names and len(names) >= 15
    assert "<base" not in html          # a <base> sends every "#view" link to /ui/#view, a 404 (lane I10a)
    assert all(n.startswith("/ui/") for n in names), names
    for name in names:
        assert client.get(name).status_code == 200, name
    root = client.get("/")
    assert b"PuffPAI by Cypher Sisters. puff puff pass" in root.data
    assert b"Nothing leaves your computer without your consent." in root.data
    css = (WEB / "desktop.css").read_text(encoding="utf-8")
    for url in re.findall(r"url\(([^)]+)\)", css):
        url = url.strip("'\"")
        assert not url.startswith("/"), url      # D108: relative to the file, so it works at /ui/ and /window/
        assert (WEB / url).is_file(), url


def test_vendored_fonts_carry_their_licenses():
    fonts = WEB / "fonts"
    for family in ("fredoka", "nunito", "jetbrains-mono"):
        assert "SIL OPEN FONT LICENSE" in (fonts / family / "OFL.txt").read_text(encoding="utf-8").upper()
    assert "Apache License" in (fonts / "material-symbols" / "LICENSE.txt").read_text(encoding="utf-8")
    assert (fonts / "LICENSE-fonts.txt").read_text(encoding="utf-8").startswith("Harvested from PuffPAIBuildBot@774e27a4:")


def _calls():
    """``(method, literal)`` for every Core route literal the Desktop's JS calls."""
    out = set()
    for path in [WEB / "desktop.js"] + sorted((WEB / "pages").glob("*.js")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r'api\("(GET|POST|PUT|DELETE)", "(/v1/[^"]*)"', src):
            out.add((m.group(1).lower(), m.group(2)))
        for m in re.finditer(r'\b(fetch|EventSource|stream)\("(/v1/[^"]*)"', src):
            post = m.group(1) == "fetch" and 'method: "POST"' in src[m.end():m.end() + 120]   # a plain fetch is a GET
            out.add(("post" if post else "get", m.group(2)))      # stream(): D79's EventSource
    return out


def _spec_matches(method, literal):
    """A literal like "/v1/toolsmith/builds/" (an id follows) or "/v1/audit?limit=60" against the contract."""
    path = literal.split("?", 1)[0]
    for spec_path, item in OPENAPI["paths"].items():
        if method not in item:
            continue
        pattern = re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(spec_path))
        if re.fullmatch(pattern, path):
            return True
        if path.endswith("/") and spec_path.startswith(path) and spec_path[len(path):].startswith("{"):
            return True
    return False


def test_every_route_a_page_calls_is_in_the_contract():
    calls = _calls()
    assert len(calls) > 40
    missing = [c for c in calls if not c[1].startswith(OTHER_LANES) and not _spec_matches(*c)]
    assert missing == []
    # the pages named in the brief use these routes
    for want in [("get", "/v1/toolsmith"), ("post", "/v1/toolsmith/plan"), ("get", "/v1/bridge/ledger?limit=500"),
                 ("get", "/v1/settings/layout"), ("post", "/v1/settings/layout/arrange"), ("get", "/v1/packages/available"),
                 ("put", "/v1/people"), ("get", "/v1/providers"), ("put", "/v1/toolsmith/model"), ("post", "/v1/chat/stream"),
                 ("post", "/v1/dev/shell"), ("get", "/v1/dev/shell"), ("get", "/v1/audit?limit=60")]:
        assert want in calls, want


def test_the_pages_set_text_never_html():
    for path in [WEB / "desktop.js", WEB / "theme.js"] + sorted((WEB / "pages").glob("*.js")):
        src = path.read_text(encoding="utf-8")
        assert "innerHTML" not in src and "outerHTML" not in src and "document.write" not in src and "eval(" not in src, path
        assert 'setAttribute("style"' not in src and "style:" not in src, path    # the Core's CSP allows no inline style
    for path in WEB.rglob("*.html"):
        assert "<script>" not in path.read_text(encoding="utf-8") and " style=" not in path.read_text(encoding="utf-8")


def test_every_harvested_text_file_says_where_it_came_from():
    for path in [WEB / "index.html", WEB / "desktop.css", WEB / "theme.js", WEB / "desktop.js", WEB / "img" / "HARVESTED.md",
                 REPO / "packages" / "cloud-avatar" / "face.html",
                 REPO / "core" / "puffpai_core" / "layout.py"] + sorted((WEB / "pages").glob("*.js")):
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert "Harvested from PuffPAIBuildBot@774e27a4:" in first, path


# -- the Home layout (v1 get_layout, save_layout, arrange) ----------------------------------------------
def test_layout_defaults_save_and_arrange(client, owner, state):
    r = client.get("/v1/settings/layout", headers=owner)
    assert r.status_code == 200
    assert r.json["tiles"]["order"] == ["apps", "building", "your-turn", "bridge", "brain"]   # D82: Your apps first
    assert r.json["tiles"]["hidden"] == ["bridge", "brain"]       # lane pages: the top bar says both; Arrange shows them
    r = client.put("/v1/settings/layout", headers=owner, json={"seen": {"tiles": {"freshtable:list": "Shopping list"}}})
    assert r.status_code == 200 and r.json["seen"]["tiles"] == {"freshtable:list": "Shopping list"}
    assert (state / "data" / "desktop" / "layout.json").is_file()

    def arrange(**body):
        return client.post("/v1/settings/layout/arrange", headers=owner, json=body)

    r = arrange(action="pin", tile="shopping list")                # her words find the tile
    assert r.status_code == 200 and r.json["id"] == "freshtable:list"
    assert r.json["layout"]["tiles"]["pinned"] == ["freshtable:list"]
    assert arrange(action="show", tile="bridge").json["layout"]["tiles"]["hidden"] == ["brain"]
    assert arrange(action="hide", tile="the bridge").json["layout"]["tiles"]["hidden"] == ["brain", "bridge"]
    moved = arrange(action="move", tile="brain", position=2).json
    assert moved["layout"]["tiles"]["order"].index("brain") == 1 and "number 2" in moved["line"]
    named = arrange(action="rename", tile="building", name="My apps").json
    assert named["layout"]["tiles"]["names"] == {"building": "My apps"}
    assert arrange(action="rename", tile="my apps", name="").json["layout"]["tiles"]["names"] == {}
    assert arrange(action="explode", tile="brain").status_code == 400
    nope = arrange(action="hide", tile="weather")
    assert nope.status_code == 400 and "could not find" in nope.json["line"]
    assert client.put("/v1/settings/layout", headers=owner, json=[1]).status_code == 400
    assert client.get("/v1/settings/layout").status_code == 401         # the owner's, nobody else's


def test_a_bad_position_is_a_plain_no_never_a_500(client, owner):
    for position in ("inf", "1e400", "-inf", "nan", "2.5", "soon", 1e300, "9999999", "0", "00", "000", 0, "-1"):
        r = client.post("/v1/settings/layout/arrange", headers=owner, json={"action": "move", "tile": "brain", "position": position})
        # five core tiles (lane pages merged What's running into Your apps): "brain" goes among the other four, so places 1 to 5
        assert r.status_code == 400 and r.json["line"] == "Say where it goes: first, last, or a number from 1 to 5.", position
    last = client.post("/v1/settings/layout/arrange", headers=owner, json={"action": "move", "tile": "brain", "position": "999999"})
    assert last.status_code == 200 and last.json["layout"]["tiles"]["order"][-1] == "brain"      # past the end: last
    second = client.post("/v1/settings/layout/arrange", headers=owner, json={"action": "move", "tile": "brain", "position": "02"})
    assert second.json["layout"]["tiles"]["order"].index("brain") == 1 and "number 2" in second.json["line"]


def test_a_huge_list_is_cut_short_fast(client, owner):
    import time
    started = time.monotonic()
    big = [f"tile-{i}" for i in range(300_000)]
    r = client.put("/v1/settings/layout", headers=owner, json={"tiles": {"order": big, "hidden": big, "pinned": big}})
    assert r.status_code == 200 and len(r.json["tiles"]["order"]) == 200
    assert time.monotonic() - started < 20


def test_a_broken_layout_file_gives_the_defaults(client, owner, state):
    path = state / "data" / "desktop" / "layout.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert client.get("/v1/settings/layout", headers=owner).json["tiles"]["pinned"] == []


# -- the built-ins not installed yet ------------------------------------------------------------------
def test_available_lists_the_built_ins_not_installed(client, owner, app, freshtable):
    r = client.get("/v1/packages/available", headers=owner)
    assert r.status_code == 200
    ids = {p["id"] for p in r.json}
    assert {"freshtable", "example-page", "cloud-avatar", "desktop"} <= ids
    assert all(set(p) == {"id", "name", "kind", "description", "version", "author", "icon"} for p in r.json)
    icons = {p["id"]: p["icon"] for p in r.json}
    assert icons["freshtable"].startswith("data:image/svg+xml;base64,")      # its own icon, for the PuffPAI store
    assert all(i is None or i.startswith("data:image/") for i in icons.values())
    app.extensions["puffpai"].registry.add(row_from_manifest(freshtable, "made"))
    assert "freshtable" not in {p["id"] for p in client.get("/v1/packages/available", headers=owner).json}
    assert client.get("/v1/packages/available").status_code == 401


# -- Puff -------------------------------------------------------------------------------------------
def test_puff_is_the_owners_own_drawing():
    pkg = REPO / "packages" / "cloud-avatar"
    art = (pkg / "puff.webp").read_bytes()
    assert art[:4] == b"RIFF" and art[8:12] == b"WEBP"
    assert (WEB / "img" / "puff.webp").read_bytes() == art          # the Desktop's own copy, the same file
    png = (pkg / "preview.png").read_bytes()
    assert png[:4] == bytes([0x89]) + b"PNG"
    assert int.from_bytes(png[16:20], "big") == 2000 and int.from_bytes(png[20:24], "big") == 2000   # full size
    assert not (pkg / "preview.svg").exists()                               # v1's drawn mascot is gone
    manifest = json.loads((pkg / "puffpai.json").read_text(encoding="utf-8"))
    assert manifest["avatar"]["preview"] == "preview.png" and manifest["icon"] == "puff.webp"
    assert manifest["ui"]["pages"] == [{"id": "face", "title": "Puff", "file": "face.html", "show": "in_desktop"}]
    assert "Puff, hand drawn by Cypher Sisters" in (pkg / "README.md").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert 'href="/ui/img/puff.webp"' in html and 'src="/ui/img/puff.webp"' in html


def test_the_desktop_shows_its_own_puff_before_setup(client):
    """A fresh bot before setup: cloud-avatar is not installed, so the Desktop's own copy is what shows."""
    assert client.get("/ui/img/puff.webp").status_code == 200
    assert 'const OWN_FACE = "/ui/img/puff.webp"' in (WEB / "logic.js").read_text(encoding="utf-8")


def test_puffs_face_is_served_from_her_own_origin(core, tmp_path):
    from puffpai_core.api.app import LISTENER, PAGES_LISTENER, wire_core
    wire_core(core)
    src = tmp_path / "cloud-avatar"
    shutil.copytree(REPO / "packages" / "cloud-avatar", src)
    assert yes(core, core.packages.add(str(src)))["status"] == "installed"
    client = core.app.test_client()
    env = {LISTENER: PAGES_LISTENER}
    r = client.get("/face.html", headers={"Host": "cloud-avatar.localhost:8422"}, environ_base=env)
    assert r.status_code == 200 and b'id="puff-mouth"' in r.data
    assert "frame-ancestors" in r.headers["Content-Security-Policy"]
    for name in ("puff.webp", "preview.png"):                     # her picture, from her own origin
        got = client.get("/" + name, headers={"Host": "cloud-avatar.localhost:8422"}, environ_base=env)
        assert got.status_code == 200 and got.data == (src / name).read_bytes(), name
    desktop = (WEB / "desktop.js").read_text(encoding="utf-8")
    assert "f.dataset.origin);" in desktop and '"*")' not in desktop      # the face's own origin, never "*"
    assert client.get("/face.html", headers={"Host": "127.0.0.1:18419"}).status_code == 404   # never on the Core's address


# -- lane I10a: the rail fits the bot, the PuffPAI store, the bot's name and theme ---------------------------
def test_the_rail_has_no_pages_a_package_would_bring():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert not (WEB / "pages" / "later.js").exists() and "later.js" not in html
    logic = (WEB / "logic.js").read_text(encoding="utf-8")
    for gone in ('id: "smarthome"', 'id: "talk"', 'id: "device"', "LATER_PAGES"):
        assert gone not in logic, gone
    assert 'id="botname"' in html


def test_the_store_and_the_bot_routes_are_called():
    calls = _calls()
    for want in [("get", "/v1/packages/available"), ("put", "/v1/settings/layout"), ("get", "/v1/settings/themes"),
                 ("put", "/v1/settings/bot")]:
        assert want in calls, want
    apps = (WEB / "pages" / "apps.js").read_text(encoding="utf-8")
    assert '["available", "PuffPAI store"]' in apps and "L.STORE_LINE" in apps
    home = (WEB / "pages" / "home.js").read_text(encoding="utf-8")
    assert '"Add apps from the PuffPAI store?"' in home and '"Not now"' in home and "L.storeNotNow(layout)" in home


def test_a_store_icon_is_only_a_small_picture_inside_its_folder(tmp_path):
    from puffpai_core.api.groups.packages import _icon_data
    (tmp_path / "a.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "big.png").write_bytes(b"x" * (64 * 1024 + 1))
    (tmp_path / "page.html").write_text("<b>x</b>", encoding="utf-8")
    assert _icon_data(tmp_path, "a.svg") == "data:image/svg+xml;base64,PHN2Zy8+"
    for bad in ("big.png", "page.html", "../a.svg", "missing.svg", "", None, 5, "C:/Windows/win.ini"):
        assert _icon_data(tmp_path, bad) is None, bad


def test_the_layout_keeps_the_store_question_hidden(client, owner):
    r = client.put("/v1/settings/layout", headers=owner,
                   json={"tiles": {"order": [], "hidden": ["store"], "pinned": [], "names": {}}})
    assert r.status_code == 200
    assert client.get("/v1/settings/layout", headers=owner).json["tiles"]["hidden"] == ["store"]

# -- lane I16: the pages for the Core's new parts ------------------------------------------------------------
def test_the_new_pages_call_their_routes_and_the_contract_has_them():
    calls = _calls()
    for want in [("get", "/v1/settings/persona"), ("put", "/v1/settings/persona"), ("post", "/v1/settings/persona/redo"),
                 ("get", "/v1/settings/you"), ("put", "/v1/settings/you"), ("get", "/v1/knowledge/sources"),
                 ("post", "/v1/packages"), ("get", "/v1/consent?status=pending"),
                 ("post", "/v1/toolsmith/builds/")]:
        assert want in calls, want
        assert _spec_matches(*want), want
    desktop = (WEB / "desktop.js").read_text(encoding="utf-8")
    # The Core adds the ticked store apps and starts the build (lane I16): the Desktop asks it to start (safe
    # twice) and shows its progress; it never adds a store app or waits on a card itself.
    after = desktop[desktop.index("async function afterPlanYes"):desktop.index("function themePicker")]
    assert '"/start", { consent_id: c.plan.consentId }' in after and "L.storeProgress(r.data)" in after
    assert '"/v1/packages"' not in after and "waitAnswer" not in desktop
    assert 'inp.kind === "theme"' in desktop and "themePicker(themes, inp.value" in desktop   # the wizard's swatches
    assert 'if (field.type === "theme") return { kind: "theme"' in (WEB / "logic.js").read_text(encoding="utf-8")


def test_no_page_names_one_kind_of_bot():
    """Goal-agnostic: the Desktop's own words never assume a smart home, voice or any one goal's pages."""
    for path in [WEB / "logic.js", WEB / "desktop.js", WEB / "index.html", WEB.parent / "README.md"] + sorted((WEB / "pages").glob("*.js")):
        text = path.read_text(encoding="utf-8").lower()
        for gone in ("smart home", "smarthome page", "listed only", "coming later", "apps step"):
            assert gone not in text, (path.name, gone)
