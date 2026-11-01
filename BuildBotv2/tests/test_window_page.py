"""The PuffPAI window (D81, PROPOSED): ``/ui/window.html`` in the Desktop's ui package.

Served by the Core at the Desktop's own name; no inline script or style (the Core's CSP); only look tokens in its CSS, so
every look works with no window code; its pure rules (tests/js/window.test.js); and its one Core route, the chat's words in
the locked form. ``tests/window_demo_core.py`` is the one to look at it, in a real browser.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
import yaml

from conftest import REPO
from puffpai_core import bot, contrast
from puffpai_core.api import create_app

WEB = REPO / "packages" / "desktop" / "web"
NODE = shutil.which("node")
FILES = ("window.html", "window.js", "window.css")
HTML = (WEB / "window.html").read_text(encoding="utf-8")
CSS = (WEB / "window.css").read_text(encoding="utf-8")
JS = (WEB / "window.js").read_text(encoding="utf-8")
OPENAPI = yaml.safe_load((REPO / "contracts" / "openapi.yaml").read_text(encoding="utf-8"))
#: Not a color of its own: what a color-mix fades into.
CSS_ALLOW = ("transparent",)


@pytest.fixture
def app(state):
    return create_app(state)


@pytest.fixture
def client(app):
    from desktop_kit import desktop_client
    return desktop_client(app)


def _css_body() -> str:
    return re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def test_the_core_serves_the_window_at_the_desktop_host(client):
    r = client.get("/ui/window.html")
    assert r.status_code == 200 and b'id="composer"' in r.data
    csp = r.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp and "default-src 'self'" in csp
    for name in re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', HTML):       # relative, for /window/ too (D108)
        assert not name.startswith("/") and client.get("/ui/" + name).status_code == 200, name
    for name in FILES:
        assert client.get("/ui/" + name).data == (WEB / name).read_bytes(), name


def test_the_window_is_not_served_on_the_plain_address(app):
    r = app.test_client().get("/ui/window.html", base_url="http://127.0.0.1:8420")
    assert r.status_code in (301, 302) or b"id=\"composer\"" not in r.data      # D79: only at the Desktop's own name


def test_the_files_are_in_the_package_and_its_check(state, monkeypatch, capsys):
    """A plain ui package's folder: the window's files are in it, and it still passes `puffpai check`."""
    files = sorted(p.relative_to(WEB).as_posix() for p in WEB.rglob("*") if p.is_file())
    for name in FILES:
        assert name in files
    from puffpai_core.packages.manifest import load
    manifest, _path = load(WEB.parent)
    assert manifest["run"]["default"] == {"type": "static", "dir": "web"}
    from puffpai_core.cli import main
    assert main(["check", str(WEB.parent)]) == 0, capsys.readouterr().out


def test_no_inline_script_or_style():
    assert "<style" not in HTML and " style=" not in HTML
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", HTML)
    assert not re.search(r"\son[a-z]+=", HTML)
    assert 'setAttribute("style"' not in JS and "cssText" not in JS


def test_only_token_colors_in_the_css():
    body = _css_body()
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b(?![\w-])", re.sub(r"[.#][\w-]+\s*[{,:. >]", " ", body)), "a hex color"
    assert not re.search(r"\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(", body), "a color function"
    known = set(re.findall(r"(--[\w-]+)\s*:", (WEB / "desktop.css").read_text(encoding="utf-8")))
    assert set(re.findall(r"var\((--[\w-]+)", body)) <= known, "a token no look or desktop.css has"
    for prop, value in re.findall(r"(?:^|[;{\s])((?:background|background-color|color|border-color|border-top-color|outline|border(?:-\w+)?))\s*:\s*([^;}]+)", body):
        words = re.sub(r"var\(--[\w-]+\)|color-mix\(in srgb,|calc\([^)]*\)|linear-gradient\(|\d+(\.\d+)?(px|%)?|[,()]", " ", value).split()
        words = [w for w in words if w not in ("solid", "none", "0") + CSS_ALLOW]
        assert not words, (prop, value)
    for value in re.findall(r"border-radius\s*:\s*([^;}]+)", body):
        assert "var(--round)" in value or value.strip() in ("0", "50%"), value    # a corner is the look's


def test_every_look_reads_on_the_window():
    """D87: the pills this page adds, on the rail, for every look by day and by night. The rest is held by test_contrast."""
    looks = list(bot.THEMES)
    for p in (REPO / "packages").glob("*/puffpai.json"):
        m = json.loads(p.read_text(encoding="utf-8"))
        if m.get("kind") == "look":
            looks.append({"id": m["id"], **m["look"]})
    assert len(looks) >= 8
    for look in looks:
        for part in contrast.PARTS:
            t = contrast.tokens_for(look, part)
            text = contrast.colour(t, "--text")
            for under in contrast.surfaces(t)["rail"]:
                for tint in ("--mint", "--red"):
                    mixed = contrast.over(contrast.colour(t, tint)[:3] + (1,), under[:3], 0.22)
                    fg = contrast.text_on(text, mixed)
                    assert contrast.ratio(fg, mixed) >= contrast.AA, (look["id"], part, tint)


def test_the_page_names_no_app_and_sets_only_text():
    assert "innerHTML" not in JS and "outerHTML" not in JS and "document.write" not in JS and "eval(" not in JS
    ids = [p.parent.name for p in (REPO / "packages").glob("*/puffpai.json")]
    for pid in ids:
        if pid in ("desktop", "wizard"):       # words in their own right
            continue
        for text in (HTML, CSS, JS):
            assert pid not in text.lower(), pid


def test_outside_apps_are_decided_by_the_frame_route_only():
    assert '"/v1/packages/" + encodeURIComponent(pkg) + "/frame"' in JS
    assert "frameSrcOk" not in JS and "isAdopted" not in JS and ".origin" not in JS   # no rule of its own that could disagree
    assert 'f.ok && f.kind === "page"' in JS
    assert JS.count("window.open(") == 2                                           # the one way out, and the dashboard's
    assert 'class: "w-frame"' in JS and JS.count('el("iframe"') == 3               # a page, her avatar's face, and that face on a Home page


def test_a_key_is_never_put_in_an_address_a_log_or_the_page():
    assert "console." not in JS
    for line in JS.splitlines():
        if "deviceKey" in line:
            assert "location" not in line and "textContent" not in line and "text:" not in line, line
    assert 'history.replaceState(null, "", location.pathname)' in JS
    assert 'localStorage.getItem(DEVICE_KEY)' in JS


def test_every_route_the_window_calls_is_in_the_contract():
    calls = set(re.findall(r'"(/v1/[a-z_/-]*[a-z_-])[/"?]', JS))
    assert "/v1/chat/stream" in calls and "/v1/consent" in calls and "/v1/events" in calls and "/v1/chat/history" in calls
    paths = list(OPENAPI["paths"])
    for call in calls:
        assert any(p == call or p.startswith(call + "/") for p in paths), call


def test_the_words_follow_the_writing_rules():
    for text in (HTML, CSS, JS, (REPO / "tests" / "js" / "window.test.js").read_text(encoding="utf-8")):
        assert chr(0x2014) not in text and "beta" not in text.lower().replace("alphabet", "") and "warts" not in text
    assert "doors" not in JS.lower() and "doors" not in HTML.lower()
    assert "Nothing leaves your computer without your consent." in JS
    assert "PuffPAI by Cypher Sisters. puff puff pass" in JS


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_pure_rules_and_the_page_parses():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "window.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stdout + got.stderr
    chk = subprocess.run([NODE, "--check", str(WEB / "window.js")], capture_output=True, text=True, timeout=60)
    assert chk.returncode == 0, chk.stderr


# -- the chat's words, for a page that keeps none of its own --------------------------------------------------------
def test_history_is_the_locked_form_for_her_paired_browser_only(app, state, client):
    from puffpai_core.zone2.conversations import ConversationStore
    store = ConversationStore(state / "chat")
    store.append("c-1", "user", "hello [[held]]")
    store.append("c-1", "assistant", "hi", changed=["some.tool"], secret_field="never sent")
    store.append("c-1", "tool", "not for the page")
    owner_key = {"Authorization": "Bearer " + app.extensions["puffpai"].tokens.issue_owner_key()}
    assert client.get("/v1/chat/history?conversation=c-1", headers=owner_key).status_code == 403
    assert app.test_client().get("/v1/chat/history?conversation=c-1").status_code in (401, 421)
    client.sign_in(app.extensions["puffpai"].tokens.issue_device("paired browser: a test"))
    headers = {"X-PuffPAI": "1"}
    got = client.get("/v1/chat/history?conversation=c-1", headers=headers)
    assert got.status_code == 200 and "no-store" in got.headers["Cache-Control"]
    assert [m["role"] for m in got.json] == ["user", "assistant"]
    assert got.json[0]["content"] == "hello [[held]]" and got.json[1]["changed"] == ["some.tool"]
    assert "secret_field" not in got.json[1]
    assert client.get("/v1/chat/history?conversation=../x", headers=headers).status_code == 400
    assert client.get("/v1/chat/history?limit=many", headers=headers).status_code == 400
    assert len(client.get("/v1/chat/history?conversation=c-1&limit=1", headers=headers).json) <= 1
    assert client.get("/v1/chat/history?conversation=none-yet", headers=headers).json == []
