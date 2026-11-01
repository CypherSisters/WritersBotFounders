"""Is a look's text readable? WCAG AA (4.5:1) for text on what it sits on (D87, D96).

``tests/test_contrast.py`` runs this on every look in the repo, so a look here that fails does not pass the
tests. At install, and in ``puffpai check``, :func:`check` runs it on a look's own manifest: it warns and never
refuses, since the owner chooses (D96).

A look sets its tokens on top of the part (day or night) the Desktop is in, so a "night" look shown by day still
has the day palette under any token it leaves out. :data:`BASE` is that palette, which is Clean's (D90), and
``tests/test_contrast.py`` holds it equal to ``desktop.css``.

What text sits on is worked out from the tokens, the way the page stacks them: the page (``--bg``, ``--bg2`` and
the glows, the cloud photo at ``--imgPage``), a panel on it (``--card``, ``--card2``, ``--railBg``, ``--deep``),
and a tile (a cloud photo at ``--imgTile`` over the card). The photos are light pastel clouds; their lightest and
darkest parts were measured (``PHOTO_*``). Text with alpha is blended first.

Only hex, ``rgb()``, ``rgba()``, ``transparent`` and ``var()`` of those can be read. Any other value is
:class:`Unreadable`, which :func:`check` says plainly.
"""
from __future__ import annotations

import itertools
import math
import re
from typing import Any, Iterator

from . import bot
from .looks import MODES, safe_tokens

AA = 4.5
PARTS = ("day", "night")

# The clouds, blurred a little: the 5th and 95th percentile pixel by lightness, from
# img/design-cloud-3.jpg (the page) and design-cloud-1, 2, 4 and 5 (the tiles).
PHOTO_PAGE = ((151, 119, 144), (230, 179, 183))
PHOTO_TILE = ((51, 137, 187), (255, 235, 240))

#: What the Desktop has under a look's tokens: Clean, its own palette (D90; the same values as ``desktop.css``).
_CLEAN = next(t for t in bot.THEMES if t["id"] == "clean")
BASE = {"day": dict(_CLEAN["day_tokens"]), "night": dict(_CLEAN["tokens"])}
#: The terminal's fixed colours in ``desktop.css`` (``.term .<word> { color: ... }``), which sit on ``--term-bg``.
TERM_COLOURS = ("#f08585", "#62d8f0")

#: Said on a Yes card, and by ``puffpai check``, for a look. Every sentence is the Core's own.
LINE_OK = "Its colours: its text reads at 4.5:1 or more, by day and by night."
LINE_LOW = ("Its colours: some of its text may be hard to read (under 4.5:1 on what it sits on). You can still add "
            "it, and pick another look at any time.")
LINE_UNKNOWN = ("Its colours: PuffPAI could not check them, so some of its text may be hard to read. You can still "
                "add it, and pick another look at any time.")


class Unreadable(ValueError):
    """A token the check can't read as a colour or a number."""


def parse(value: Any) -> tuple | None:
    """``(r, g, b, a)`` for a hex or rgb()/rgba() colour, else None. Channels are kept in range."""
    v = str(value).strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)", v)
    if m:
        try:
            r, g, b, a = (float(x) for x in (m.group(1), m.group(2), m.group(3), m.group(4) or "1"))
        except ValueError:
            return None
        return (min(r, 255.0), min(g, 255.0), min(b, 255.0), min(a, 1.0))
    if v == "transparent":
        return (0, 0, 0, 0.0)
    return None


def over(top: tuple, under: tuple, alpha: float | None = None) -> tuple:
    """*top* (rgba) laid on *under* (rgb), with *alpha* in place of its own if given."""
    a = top[3] if alpha is None else alpha
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3))


def luminance(rgb: tuple) -> float:
    def chan(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple, b: tuple) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens_for(look: dict, part: str, base: dict | None = None) -> dict:
    """The tokens on :root, as theme.js leaves them (logic.themeTokensFor)."""
    own = look["day_tokens"] if look["mode"] == "both" and part == "day" else look["tokens"]
    return {**(BASE if base is None else base)[part], **own}


def colour(t: dict, name: str, depth: int = 0) -> tuple:
    raw = str(t.get(name, "")).strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", raw)
    if m and depth < 5:
        return colour(t, m.group(1), depth + 1)
    got = parse(raw)
    if got is None:
        raise Unreadable(f"{name}: {raw}")
    return got


def number(t: dict, name: str) -> float:
    raw = str(t.get(name, "")).strip()
    try:
        got = float(raw)
    except ValueError:
        raise Unreadable(f"{name}: {raw}") from None
    if not math.isfinite(got):
        raise Unreadable(f"{name}: {raw}")
    return min(max(got, 0.0), 1.0)


def accent_stops(t: dict) -> list:
    """``--accent`` is a colour or a gradient of var() colours: each stop, as text sits on all of it."""
    raw = str(t.get("--accent", ""))
    names = re.findall(r"var\((--[\w-]+)\)", raw)
    if names:
        return [colour(t, n) for n in names]
    return [colour(t, "--accent")]


def surfaces(t: dict) -> dict:
    """Every background each kind of text can end up on: page, card, tile, rail, chip, deep."""
    bg, bg2 = colour(t, "--bg"), colour(t, "--bg2")
    base = [bg[:3], bg2[:3]]
    for glow in ("--glow1", "--glow2"):
        g = colour(t, glow)
        base += [over(g, bg[:3]), over(g, bg2[:3])]
    page_alpha, tile_alpha = number(t, "--imgPage"), number(t, "--imgTile")
    page = base + [over(p + (1,), b, page_alpha) for b in base for p in PHOTO_PAGE]

    def on(name: str, under: list) -> list:
        c = colour(t, name)
        return [over(c, u) for u in under]
    card = on("--card", page) + on("--card2", page)
    tile = [over(p + (1,), u, tile_alpha) for u in on("--card", page) for p in PHOTO_TILE] + on("--card", page)
    rail = on("--railBg", page)
    return {"page": page, "card": card, "tile": tile, "rail": rail,
            "chip": on("--chip", page + card + tile + rail), "deep": on("--deep", page)}


def text_on(fg: tuple, under: tuple) -> tuple:
    """The colour text ends up: its own alpha, blended on what it sits on."""
    return over(fg, under)


# Each pair: (what, text token, the surfaces it sits on). The rules in desktop.css; "chip" is a --chip fill
# (state pills, wizard steps, launchers, the bot name, a fold's count) on any of the others.
TEXT = [
    ("body text (and launchers)", "--text", ("page", "card", "tile", "rail", "deep", "chip")),
    ("second text (.muted, rail, chips, pills, footer)", "--text2", ("page", "card", "tile", "rail", "chip")),
    ("faint text (.faint, .micro, hints, a stopped app)", "--text3", ("page", "card", "tile", "rail", "chip")),
    ("links (a, button.link, .now)", "--sky", ("page", "card", "tile")),
    ("accent text (.big, .persona-role, edit bar, bot name, live pill)", "--pink", ("card", "tile", "chip")),
    ("good (.ok-t, good pill)", "--mint", ("card", "tile", "chip")),
    ("warning (.warn-t, .fail, .err, chip.warn, stop pill)", "--lemon", ("card", "tile", "page", "chip")),
    ("problem (.bad-t, button.danger)", "--red", ("card", "tile")),
]


def pairs(look: dict, part: str, base: dict | None = None, term: Any = None) -> Iterator[tuple]:
    """Every (what, text, background, ratio) for one look at one part of the day."""
    t = tokens_for(look, part, base)
    where = surfaces(t)
    for what, name, on in TEXT:
        fg = colour(t, name)
        for u in itertools.chain.from_iterable(where[s] for s in on):
            yield what, name, u, ratio(text_on(fg, u), u)
    for stop in accent_stops(t):
        yield "text on accent (button.primary, the rail's page, my chat)", "--on-primary", stop[:3], \
            ratio(colour(t, "--on-primary")[:3], stop[:3])
    pink = colour(t, "--pink")[:3]
    yield "a badge (.badge)", "--on-accent", pink, ratio(colour(t, "--on-accent")[:3], pink)
    bg = colour(t, "--term-bg")[:3]
    yield "terminal text", "--term-fg", bg, ratio(colour(t, "--term-fg")[:3], bg)
    for hard in (TERM_COLOURS if term is None else term):
        yield f"terminal {hard}", hard, bg, ratio(parse(hard)[:3], bg)


def failures(look: dict, part: str, base: dict | None = None, term: Any = None) -> list[str]:
    """One plain line per kind of text under 4.5:1. Raises :class:`Unreadable` on a value it can't read."""
    worst: dict = {}
    for what, name, under, r in pairs(look, part, base, term):
        if r < AA and (what not in worst or r < worst[what][0]):
            worst[what] = (r, name, under)
    return [f"{look['id']} {part}: {what} ({name}) {r:.2f}:1 on rgb{tuple(round(c) for c in u)}"
            for what, (r, name, u) in worst.items()]


def check(manifest: Any) -> dict:
    """``{"state": "ok" | "low" | "unknown", "line", "failed"}`` for a ``look`` package's manifest. Never raises:
    a look with values it can't read is ``unknown``. It warns, and never refuses a look."""
    try:
        block = manifest["look"]
        if not isinstance(block["tokens"], dict):
            raise Unreadable("look.tokens")
        look = {"id": str(manifest.get("id") or "look"),
                "mode": block.get("mode") if block.get("mode") in MODES else "both",
                "tokens": safe_tokens(block["tokens"]), "day_tokens": safe_tokens(block.get("day_tokens"))}
        failed = [f for part in PARTS for f in failures(look, part)]
    except Exception:  # noqa: BLE001 - a look's odd values must never stop an install or a check
        return {"state": "unknown", "line": LINE_UNKNOWN, "failed": []}
    return {"state": "low", "line": LINE_LOW, "failed": failed} if failed else \
        {"state": "ok", "line": LINE_OK, "failed": []}


__all__ = ["AA", "BASE", "LINE_LOW", "LINE_OK", "LINE_UNKNOWN", "PARTS", "PHOTO_PAGE", "PHOTO_TILE", "TERM_COLOURS",
           "TEXT", "Unreadable", "check", "failures", "luminance", "ratio"]
