"""Lane looks (D76): looks and simple avatars in the PuffPAI store, and fewer tiles.

The owner's ask: "a few different UI choices, a couple simple avatars... one really clean one, simple colors,
squares instead of rounded corners, clean and coder looking." (Clean became the built-in default in D90,
lane clean-default; the other looks stay in the store.) A look is a package of kind ``look``: values
only (CSS variables the Desktop already reads), no CSS file, no script, nothing that loads anything. An
avatar is the existing kind, a plain SVG drawing. Each installs through the real Package Manager (its Yes
card, the real Drawbridge), shows on Looks, can be picked, and is gone again when removed.
"""

import json
import re
import shutil
import subprocess

import pytest

from conftest import REPO, wait_for, yes
from puffpai_core import bot, desktop, looks
from puffpai_core.packages import manifest

PACKAGES = REPO / "packages"
WEB = PACKAGES / "desktop" / "web"
NODE = shutil.which("node")
LOOKS = ["look-contrast", "look-paper", "look-slate"]
AVATARS = ["avatar-block", "avatar-pebble"]


def _owner(core):
    return {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}


def _device(core):
    return {"Authorization": f"Bearer {core.tokens.issue_device('laptop')}"}


def _raw(pid):
    return json.loads((PACKAGES / pid / "puffpai.json").read_text(encoding="utf-8"))


def _desktop_names():
    """Every CSS variable the Desktop sets: its palettes and :root."""
    css = (WEB / "desktop.css").read_text(encoding="utf-8")
    return set(re.findall(r"(--[A-Za-z0-9-]+)\s*:", css))


# -- 1. the store items as shipped -----------------------------------------------------------------------

@pytest.mark.parametrize("pid", LOOKS + AVATARS)
def test_each_store_item_is_valid_and_its_files_are_there(pid):
    data, _ = manifest.load(PACKAGES / pid)
    assert manifest.validate(data) == [] and manifest.check_files(PACKAGES / pid, data) == []
    assert data["author"] == "Cypher Sisters" and "run" not in data and "abilities" not in data
    text = json.dumps(data) + (PACKAGES / pid / "README.md").read_text(encoding="utf-8")
    assert chr(0x2014) not in text and "beta" not in text.lower() and "doors" not in text.lower()


@pytest.mark.parametrize("pid", LOOKS)
def test_a_look_sets_only_names_the_desktop_reads_with_safe_values(pid):
    block = _raw(pid)["look"]
    known = _desktop_names()
    assert block["mode"] == "both" and block["line"]                    # light and dark, each of them
    for part in ("tokens", "day_tokens"):
        assert set(block[part]) <= known, set(block[part]) - known        # no typo goes unseen
        assert looks.token_problems(block[part], part) == []
        assert looks.safe_tokens(block[part]) == {k: str(v) for k, v in block[part].items()}


@pytest.mark.parametrize("pid", LOOKS)
def test_a_look_is_whole_its_buttons_read_and_its_preview_shows_its_real_colours(pid):
    """Store polish (1.1.0): both parts set the same names, --nest included; the button text and the accent
    on the ground read (WCAG, 4.5:1 and 3:1); preview.svg shows night on the left and day on the right."""
    from puffpai_core.contrast import parse, ratio
    block = _raw(pid)["look"]
    assert set(block["tokens"]) == set(block["day_tokens"]) and "--nest" in block["tokens"]
    svg = (PACKAGES / pid / "preview.svg").read_text(encoding="utf-8")
    night, day = svg.split('<g transform="translate(200,0)">')
    for part, half in (("tokens", night), ("day_tokens", day)):
        t = {k: parse(v) for k, v in block[part].items() if parse(v)}
        assert ratio(t["--on-primary"], t["--accent"]) >= 4.5 and ratio(t["--on-accent"], t["--pink"]) >= 4.5, part
        assert ratio(t["--accent"], t["--bg"]) >= 3, part
        for name in ("--bg", "--accent", "--text", "--text2", "--text3", "--card", "--card2", "--line"):
            assert f'"{block[part][name]}"' in half, (part, name)
    assert "href" not in svg and "<script" not in svg and len(svg) < 8192


def _clean():
    return next(t for t in bot.THEMES if t["id"] == "clean")


def test_the_clean_look_is_square_flat_and_mono():
    block = _clean()
    for part in (block["tokens"], block["day_tokens"]):
        assert part["--round"] == "0" and part["--glass"] == "0" and part["--shine"] == "0" and part["--nest"] == "0"
        assert part["--imgPage"] == part["--imgTile"] == part["--stars"] == "0"      # no cloud picture, no stars
        assert part["--shadow"] == "none" and part["--bg"] == part["--bg2"]          # no gradient
        assert "gradient" not in part["--accent"] and "JetBrains Mono" in part["--display"]
    assert block["tokens"]["--bg"] != block["day_tokens"]["--bg"]                    # dark and light


def test_every_built_in_look_passes_the_look_rules_whole():
    """The built-in looks (Clean and v1's shape on Puff and Aero, D90) keep every value through the same
    check a look package gets, so nothing is dropped on the way to :root."""
    for t in bot.THEMES:
        for part in [t["tokens"]] + ([t["day_tokens"]] if "day_tokens" in t else []):
            assert len(part) <= looks.MAX_TOKENS and looks.token_problems(part, t["id"]) == [], t["id"]
            assert looks.safe_tokens(part) == {k: str(v) for k, v in part.items()}, t["id"]


def test_every_corner_and_glass_in_the_desktop_follows_the_look():
    css = (WEB / "desktop.css").read_text(encoding="utf-8")
    fixed = [m for m in re.findall(r"border(?:-[a-z]+)*-radius:\s*([^;]+);", css) if "var(--round)" not in m]
    assert fixed == ["inherit"]
    assert not re.search(r"backdrop-filter: blur\(\d", css)
    assert "--round: 0; --glass: 0; --shine: 0; --nest: 0;" in css         # the Desktop's own shape is Clean's (D90)
    for t in bot.THEMES:                                                  # Puff and Aero keep v1's round, glassy shape
        if t["id"] != "clean":
            for part in [t["tokens"]] + ([t["day_tokens"]] if "day_tokens" in t else []):
                assert {k: part[k] for k in bot._V1_SHAPE} == bot._V1_SHAPE, t["id"]   # noqa: SLF001
    assert bot._V1_SHAPE["--round"] == bot._V1_SHAPE["--nest"] == "1"   # noqa: SLF001


def test_the_desktops_own_look_is_the_built_in_clean():
    """desktop.css's day and night blocks and :root hold Clean's values, so the Desktop looks the same
    before the Core answers, and when it has no list of looks (D90)."""
    from test_contrast import PALETTE, ROOT
    clean = _clean()
    for part, mine in (("night", clean["tokens"]), ("day", clean["day_tokens"])):
        css = {**ROOT, **PALETTE[part]}
        assert {k: css.get(k) for k in mine} == {k: str(v) for k, v in mine.items()}, part


def test_nothing_inside_a_panel_is_framed_under_clean():
    """No card inside a card under Clean (D90): the boxes inside a panel take their fill and border from
    --nest, which Clean sets to 0 and Puff and Aero to 1."""
    css = (WEB / "desktop.css").read_text(encoding="utf-8")
    mixed = "color-mix(in srgb, var(--line) calc(var(--nest) * 100%), transparent)"
    for sel in (".tile .card, .card .card", ".looks-preview", ".swatch", ".launcher"):
        rule = re.search(r"(?m)^" + re.escape(sel) + r" \{([^}]*)\}", css).group(1)
        assert "calc(var(--nest) * 100%)" in rule and "var(--line)" not in rule.replace(mixed, ""), sel


def test_clean_is_the_default_for_a_new_bot_and_the_footer_is_untouched():
    assert bot.DEFAULT_THEME == "clean" and bot.THEMES[0]["id"] == "clean"
    assert bot.read(REPO / "no-such-state")["theme"] == "clean"
    assert "puff" in bot.theme_ids()                                       # Puff stays a choice in Looks
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "<span>Nothing leaves your computer without your consent.</span>" in html
    assert "<b>PuffPAI by Cypher Sisters. puff puff pass</b>" in html


# -- 2. what a look or a picture may not have -------------------------------------------------------------

def _look(**over):
    m = {"schema": "puffpai-package/1", "id": "my-look", "kind": "look", "name": "Mine", "version": "1.0.0",
         "description": "A look.", "author": "Someone", "license": "MIT", "consent": "Colours only.",
         "look": {"mode": "night", "line": "Dark.", "tokens": {"--bg": "#000000"}}}
    m.update(over)
    return m


def test_a_plain_look_is_valid():
    assert manifest.validate(_look()) == []


@pytest.mark.parametrize("value", [
    "url(https://example.com/x.png)", "URL( //x )", "image(\"x.png\")", "image-set(\"a.png\" 1x)", "@import x",
    "red; background: blue", "#fff} body {", "expression(alert(1))", "src(\"x\")", "<b>", "a\\75 rl(x)",
    "cross-fade(a, b)", "element(#x)", "attr(title)", "", "x" * 121,
])
def test_a_value_that_could_load_something_or_break_out_is_refused(value):
    problems = manifest.validate(_look(look={"mode": "night", "line": "Dark.", "tokens": {"--bg": value}}))
    assert problems, value
    assert looks.safe_tokens({"--bg": value}) == {}


def test_safe_values_pass():
    ok = {"--bg": "#0F1115", "--accent": "linear-gradient(140deg, #fff, rgba(0, 0, 0, 0.5))", "--round": 0,
          "--display": '"JetBrains Mono", ui-monospace, monospace', "--gap": "clamp(8px, 1vw, 12px)",
          "--glow": "color-mix(in srgb, var(--pink) 40%, transparent)", "--tile-pad": "12px 14px"}
    assert looks.token_problems(ok, "look.tokens") == [] and looks.safe_tokens(ok) == {k: str(v) for k, v in ok.items()}


@pytest.mark.parametrize("over, says", [
    ({"look": {"mode": "both", "line": "x", "tokens": {"--bg": "#000"}}}, "day_tokens"),
    ({"look": {"mode": "night", "line": "x", "tokens": {"bg": "#000"}}}, "--"),
    ({"look": {"mode": "dusk", "line": "x", "tokens": {"--bg": "#000"}}}, "mode"),
    ({"look": {"mode": "night", "line": "x", "tokens": {"--bg": "#000"}, "css": "look.css"}}, "{mode, line, tokens}"),
    ({"run": {"default": {"type": "static", "dir": "."}}}, "runs nothing"),
    ({"abilities": {"mcp": {"type": "stdio", "command": ["x"]}}}, "runs nothing"),
    ({"look": None}, "look"),
])
def test_a_look_with_the_wrong_shape_or_a_process_is_refused(over, says):
    m = _look(**over)
    if over.get("look") is None and "look" in over:
        del m["look"]
    problems = manifest.validate(m)
    assert problems and any(says in p for p in problems), problems


@pytest.mark.parametrize("svg", [
    '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" onload="x()"><rect/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/a.png"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="other.svg#a"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://x/y)"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div/></foreignObject></svg>',
    '<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>',
])
def test_an_avatar_picture_with_script_or_an_outside_link_is_refused(tmp_path, svg):
    (tmp_path / "face.svg").write_text(svg, encoding="utf-8")
    data = {"kind": "avatar", "avatar": {"preview": "face.svg"}}
    problems = manifest.check_files(tmp_path, data)
    assert problems and problems[0].startswith("avatar.preview: the picture has"), problems


def test_a_plain_drawing_with_an_inner_link_is_fine(tmp_path):
    (tmp_path / "face.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="g"/></defs>'
                                       '<rect fill="url(#g)"/><use href="#g"/></svg>', encoding="utf-8")
    assert manifest.check_files(tmp_path, {"kind": "avatar", "avatar": {"preview": "face.svg"}}) == []


# -- 3. in the store, installed, picked, removed ------------------------------------------------------------

def test_looks_and_avatars_show_in_the_store(core):
    listed = {p["id"]: p for p in core.app.test_client().get("/v1/packages/available", headers=_owner(core)).json}
    for pid in LOOKS:
        assert listed[pid]["kind"] == "look" and listed[pid]["icon"].startswith("data:image/svg+xml;base64,")
    for pid in AVATARS:
        assert listed[pid]["kind"] == "avatar"


def test_install_pick_and_remove_a_look(core):
    client = core.app.test_client()
    ids = lambda: [t["id"] for t in client.get("/v1/settings/themes", headers=_owner(core)).json]  # noqa: E731
    assert "look-slate" not in ids()
    assert client.put("/v1/settings/bot", json={"theme": "look-slate"}, headers=_device(core)).status_code == 400

    staged = core.packages.add("look-slate")                             # the store's built-in id, its Yes card
    assert staged["consent_id"] and staged["floor"] is False
    assert yes(core, staged)["status"] == "installed"
    assert wait_for(lambda: (core.registry.get("look-slate") or {}).get("status") == "stopped", 30)
    themes = client.get("/v1/settings/themes", headers=_owner(core)).json
    assert [t["id"] for t in themes][0] == "clean" and "look-slate" in [t["id"] for t in themes]
    slate = next(t for t in themes if t["id"] == "look-slate")
    assert slate["name"] == "Slate" and slate["mode"] == "both" and slate["tokens"]["--round"] == "0.35"
    assert slate["day_tokens"]["--bg"] == "#EEF1F4" and slate["preview"] == "/pkg/look-slate/preview.svg"
    assert "look-slate" not in {p["id"] for p in client.get("/v1/packages/available", headers=_owner(core)).json}

    r = client.put("/v1/settings/bot", json={"theme": "look-slate"}, headers=_device(core))
    assert r.status_code == 200 and r.json["bot"]["theme"] == "look-slate"
    assert core.steward.pids("look-slate") == []                           # a look runs nothing

    r = client.delete("/v1/packages/look-slate?keep_data=delete", headers=_owner(core))
    assert r.status_code == 200 and r.json["removed"]
    assert "look-slate" not in ids()
    assert client.get("/v1/settings", headers=_owner(core)).json["bot"]["theme"] == "clean"  # back to the default
    assert client.put("/v1/settings/bot", json={"theme": "look-slate"}, headers=_device(core)).status_code == 400


def test_a_changed_look_file_is_not_offered(core, state):
    staged = core.packages.add("look-paper")
    assert yes(core, staged)["status"] == "installed"
    assert "look-paper" in bot.theme_ids(state, core.registry)
    from puffpai_core.packages.installs import Installs
    path = Installs(state).folder("look-paper") / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8").replace("#F5EFE3", "#000000"), encoding="utf-8")
    assert "look-paper" not in bot.theme_ids(state, core.registry)       # only the file her Yes covered


def test_a_package_never_stands_in_for_a_built_in_look(core, state):
    from puffpai_core.registry import row_from_manifest
    core.registry.add(row_from_manifest(_look(id="puff"), "made", status="stopped"))
    assert [t["id"] for t in bot.themes(state, core.registry)].count("puff") == 1


def test_adding_an_avatar_never_changes_her_face_by_itself(core, state):
    import time
    for pid in ("cloud-avatar", "avatar-block"):
        assert yes(core, core.packages.add(pid))["status"] == "installed"
        time.sleep(1.1)                       # installed_at is to the second; setup's avatar is minutes older
    client = core.app.test_client()
    assert client.get("/v1/settings", headers=_owner(core)).json["avatar"]["id"] == "cloud-avatar"
    r = client.put("/v1/settings/avatar", json={"id": "avatar-block"}, headers=_owner(core))
    assert r.status_code == 200 and r.json["avatar"]["preview"] == "/pkg/avatar-block/face.svg"
    assert desktop.avatar_view(state, core.registry)["name"] == "Block"


# -- 4. the Desktop's own logic (node) --------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_looks_logic():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "looks.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_touched_desktop_files_parse():
    for name in ("logic.js", "desktop.js", "pages/vault.js", "pages/apps.js", "pages/settings.js"):
        got = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, (name, got.stderr)


def test_the_vault_page_is_one_list_not_a_card_each():
    src = (WEB / "pages" / "vault.js").read_text(encoding="utf-8")
    assert 'class: "card"' not in src and '"actlist"' in src and "innerHTML" not in src
    for title in ("Set up your vault", "Lock it now", "Unlock", "Change your PIN", "Make a new recovery code",
                  "Test your recovery code", "Get back in with your recovery code", "Ways in",
                  "Add a passphrase as a way in", "Inbox", "Moving and backing up the vault"):
        assert f'"{title}"' in src, title                                  # every action is still there
