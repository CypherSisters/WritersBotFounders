"""Lane contrast: every look's text is readable, WCAG AA (4.5:1) for text on what it sits on.

Every built-in look (``bot.THEMES``) and every look package (``packages/*/puffpai.json`` with
``"kind": "look"``), by day and by night, with the Desktop's own palettes under it (``desktop.css``):
a look sets its tokens on top of the part (day or night) the Desktop is in, so a "night" look shown
by day still has the day palette under any token it leaves out.

The calculation is the Core's own (``puffpai_core.contrast``, D96), so an outside look gets the same check at
install: what text sits on, worked out from the tokens the way the page stacks them, with the measured photos
(``PHOTO_*``, checked again below when Pillow is there). No text may be faded with ``opacity`` (the rules allowed are listed).
"""
from __future__ import annotations

import json
import re

import pytest

from conftest import REPO
from puffpai_core import bot, contrast
from puffpai_core.contrast import PHOTO_PAGE, PHOTO_TILE, luminance, ratio

WEB = REPO / "packages" / "desktop" / "web"
CSS = (WEB / "desktop.css").read_text(encoding="utf-8")
PARTS = contrast.PARTS

# The clouds, measured (contrast.PHOTO_*): the 5th and 95th percentile pixel by lightness.
TILE_PHOTOS = ("design-cloud-1.jpg", "design-cloud-2.jpg", "design-cloud-4.jpg", "design-cloud-5.jpg")


# -- the tokens a look ends up with ------------------------------------------------------------------------
def _block(selector: str) -> dict:
    m = re.search(re.escape(selector) + r"[^{]*\{([^}]*)\}", CSS)
    assert m, selector
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(1)))


ROOT = _block(":root")
PALETTE = {"day": _block('html[data-puff-theme="day"]'), "night": _block('html[data-puff-theme="night"]')}
#: What a look has under it, read from desktop.css itself (the Core's own copy is held equal to this below).
BASE = {part: {**ROOT, **PALETTE[part]} for part in PARTS}


def looks() -> list[dict]:
    out = [dict(t, store=False) for t in bot.THEMES]
    for path in sorted((REPO / "packages").glob("*/puffpai.json")):
        m = json.loads(path.read_text(encoding="utf-8"))
        if m.get("kind") == "look":
            b = m["look"]
            out.append({"id": m["id"], "mode": b["mode"], "tokens": b["tokens"],
                        "day_tokens": b.get("day_tokens") or {}, "store": True})
    return out


# The only selectors allowed to fade with opacity, each matched whole: they fade no text she has to read (a
# disabled button, the vault bars, the stars, the photos, a page sliding in, a blinking live dot or typing
# dot, and the Looks swatches, which show another look's own colours).
OPACITY_OK = {"button:disabled", ".private .bars i", "body::before", ".scene", ".tile::before", ".view", ".dot.live",
              ".msg.bot .bubble.typing::after", ".swatch-mini small"}


def _faded(value: str) -> bool:
    v = value.strip()
    if v.startswith("var("):
        return True
    try:
        return (float(v[:-1]) / 100 if v.endswith("%") else float(v)) < 1
    except ValueError:
        return True                                                    # can't tell: count it as faded


def faded_rules(css: str = CSS) -> list[str]:
    """Every selector that fades: an ``opacity`` or ``filter: opacity()`` under 1, or an animation whose
    keyframes fade (a transition naming opacity does not count). Selector lists come apart, one each."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    fading = set()
    for name, body in re.findall(r"@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*)[^{}]*\}", css):
        if any(_faded(v) for v in re.findall(r"(?<![-\w])opacity:\s*([^;}]+)", body)):
            fading.add(name)
    css = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        values = re.findall(r"(?<![-\w])opacity:\s*([^;}]+)", body) + re.findall(r"opacity\(\s*([^)]+)\)", body)
        anim = re.findall(r"animation(?:-name)?:\s*([^;}]+)", body)
        if any(_faded(v) for v in values) or any(w in fading for a in anim for w in re.findall(r"[\w-]+", a)):
            out += [" ".join(s.split()) for s in sel.split(",") if s.strip()]
    return out


TERM_COLOURS = re.findall(r"\.term \.\w+ \{ color: (#[0-9a-fA-F]{3,6}); \}", CSS)


def failures(look: dict, part: str) -> list[str]:
    return contrast.failures(look, part, BASE, TERM_COLOURS)


ALL = [(lk, part) for lk in looks() for part in PARTS]


@pytest.mark.parametrize("look,part", ALL, ids=[f"{lk['id']}-{p}" for lk, p in ALL])
def test_every_look_has_readable_text(look, part):
    assert failures(look, part) == []


def test_no_text_is_faded_with_opacity():
    """Text faded with ``opacity`` sits below the model; the colours above are the way to make text quieter."""
    assert [s for s in faded_rules() if s not in OPACITY_OK] == []
    for sample in (".tile::before, .card .from { opacity: .4 }", ".rail a.item.on .label { opacity: .5 }",
                   ".scene-caption { opacity: 40% }", ".muted { filter: opacity(.4) }",
                   "@keyframes fadey { 0% { opacity: 1; } 50% { opacity: .3; } } .muted { animation: fadey 1s; }"):
        assert [s for s in faded_rules(sample) if s not in OPACITY_OK], sample


def test_every_look_is_checked():
    ids = {lk["id"] for lk in looks()}
    assert {"clean", "puff", "puff-night", "puff-day", "aero", "aero-night"} <= ids
    assert {"look-paper", "look-contrast", "look-slate"} <= ids
    assert {lk["id"] for lk in looks() if lk["store"]} == {
        p.parent.name for p in (REPO / "packages").glob("*/puffpai.json")
        if json.loads(p.read_text(encoding="utf-8")).get("kind") == "look"}


def test_the_cores_palette_is_the_desktops():
    """The Core checks an outside look against its own copy of what sits under it: never out of step with the CSS."""
    for part in PARTS:
        assert set(PALETTE[part]) <= set(contrast.BASE[part]), part
        assert {k: " ".join(contrast.BASE[part][k].split()) for k in PALETTE[part]} == \
            {k: " ".join(v.split()) for k, v in PALETTE[part].items()}, part
    assert list(contrast.TERM_COLOURS) == TERM_COLOURS


def test_the_model_knows_a_bad_pair_when_it_sees_one():
    assert round(ratio((0, 0, 0), (255, 255, 255)), 1) == 21.0
    assert round(ratio((119, 119, 119), (255, 255, 255)), 2) == 4.48               # #777 on white: just under
    bad = {"id": "bad", "mode": "night", "day_tokens": {}, "store": True,
           "tokens": {"--text3": "#6A6A8A", "--imgPage": "0.42"}}
    assert any("faint text" in f for f in failures(bad, "night"))


def test_the_photo_numbers_still_match_the_photos():
    pil = pytest.importorskip("PIL.Image")
    flt = pytest.importorskip("PIL.ImageFilter")

    def ends(name):
        im = pil.open(WEB / "img" / name).convert("RGB")
        im = im.resize((max(1, im.width // 8), max(1, im.height // 8))).filter(flt.GaussianBlur(2))
        raw = im.tobytes()
        px = sorted((tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)), key=luminance)
        return px[len(px) // 20], px[len(px) * 19 // 20]
    dark, light = ends("design-cloud-3.jpg")
    assert luminance(dark) >= luminance(PHOTO_PAGE[0]) - 0.01 and luminance(light) <= luminance(PHOTO_PAGE[1]) + 0.01
    for name in TILE_PHOTOS:
        dark, light = ends(name)
        assert luminance(dark) >= luminance(PHOTO_TILE[0]) - 0.01, name
        assert luminance(light) <= luminance(PHOTO_TILE[1]) + 0.01, name
