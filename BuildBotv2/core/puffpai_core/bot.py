r"""The bot's own name and look (lane I10c): ``settings\bot.json``.

``{"name": "Puff", "theme": "clean"}`` until the owner picks (D90: Clean is the default look for a new
bot; a bot that finished setup before it had a look saved keeps Puff, :data:`SET_UP_THEME`). The wizard's
two steps ("Name your bot", "Pick its look") and ``PUT /v1/settings/bot`` both save
through :func:`save`, so one set of rules holds everywhere.

Clean (D90, first and the default) is the Desktop's own palette, ``desktop.css``: square, flat, no glow,
no clouds. The other looks are the palettes v1 shipped as its own (no catalog data from other
projects): Puff is v1's ``dashboard/css/_theme.css`` night and day, harvested
from ``PuffPAIBuildBot@774e27a4`` (copied); Aero is v1's first app palette,
``src/puffpai/ui/theme.py`` at the same commit (adapted to the Desktop's
tokens). Puff and Aero also set v1's shape (:data:`_V1_SHAPE`: round corners, glass, glow, v1's fonts),
since the Desktop's own shape is Clean's. Every token name is one the Desktop already uses
(``packages/desktop/web/desktop.css``, the ``data-puff-theme`` blocks and ``:root``).

A theme is ``{id, name, mode, line, tokens}``. ``mode`` is ``night``, ``day``
or ``both``. A ``both`` theme follows the Desktop's day and night switch: its
``tokens`` are the night palette and ``day_tokens`` the day palette.

Installed ``look`` packages (D76, ``puffpai_core.looks``) are offered after these, by name, when the
caller passes the state folder and the Registry; a look that was removed reads as the default.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

from . import jsonfile

DEFAULT_NAME = "Puff"
DEFAULT_THEME = "clean"
#: The look of a bot whose setup finished before a look was saved: Puff, the default before D90.
SET_UP_THEME = "puff"
NAME_MAX = 40
FILE = ("settings", "bot.json")

# Lane clean-default (D90): Clean, moved here from the store's look package (lane looks, D76; lane contrast,
# D87). The same values as desktop.css's day and night blocks and :root, so the Desktop looks the same before
# the Core answers.
_CLEAN_SHAPE = {
    "--accent": "#2F6FEB", "--round": "0", "--glass": "0", "--shine": "0", "--nest": "0",
    "--gap": "8px", "--tile-pad": "12px 14px", "--edge": "2px",
    "--display": "\"JetBrains Mono\", ui-monospace, Consolas, monospace",
    "--body": "\"Segoe UI\", system-ui, sans-serif",
    "--text-size": "14px", "--body-weight": "400", "--head-weight": "700",
}
_CLEAN_NIGHT = {
    "--bg": "#0F1115", "--bg2": "#0F1115", "--card": "#14171C", "--card2": "#111418", "--line": "#262B33",
    "--text": "#E6E8EB", "--text2": "#A0A7B0", "--text3": "#838A94",
    "--pink": "#5B9BFF", "--magenta": "#3B74E0", "--sky": "#5B9BFF", "--mint": "#3FB27F",
    "--lemon": "#D9A440", "--lav": "#8B95A5",
    "--railBg": "#0C0E11", "--chip": "#1C2027", "--red": "#E5534B", "--on-accent": "#0F1115",
    "--deep": "#0B0D10", "--on-primary": "#FFFFFF",
    "--glow1": "rgba(0, 0, 0, 0)", "--glow2": "rgba(0, 0, 0, 0)", "--glow": "rgba(0, 0, 0, 0)", "--shadow": "none",
    "--imgTile": "0", "--imgPage": "0", "--img2": "0", "--stars": "0",
    "--term-bg": "#0B0D10", "--term-fg": "#E6E8EB",
    **_CLEAN_SHAPE,
}
_CLEAN_DAY = {
    "--bg": "#FAFAF9", "--bg2": "#FAFAF9", "--card": "#FFFFFF", "--card2": "#F4F4F2", "--line": "#DADAD6",
    "--text": "#16181C", "--text2": "#555B64", "--text3": "#656972",
    "--pink": "#1F5FD6", "--magenta": "#1A4FB3", "--sky": "#1F5FD6", "--mint": "#1C774F",
    "--lemon": "#8D6100", "--lav": "#5B6472",
    "--railBg": "#F2F2F0", "--chip": "#ECECE9", "--red": "#C4362C", "--on-accent": "#FFFFFF",
    "--deep": "#FFFFFF", "--on-primary": "#FFFFFF",
    "--glow1": "rgba(0, 0, 0, 0)", "--glow2": "rgba(0, 0, 0, 0)", "--glow": "rgba(0, 0, 0, 0)", "--shadow": "none",
    "--imgTile": "0", "--imgPage": "0", "--img2": "0", "--stars": "0",
    "--term-bg": "#16181C", "--term-fg": "#FAFAF9",
    **_CLEAN_SHAPE, "--accent": "#1F5FD6",
}
# v1's shape, which was desktop.css's :root until D90: Puff and Aero set it themselves now.
_V1_SHAPE = {
    "--accent": "linear-gradient(140deg, var(--pink), var(--magenta))",
    "--round": "1", "--glass": "1", "--shine": "1", "--nest": "1",
    "--gap": "clamp(12px, 1.2vw, 18px)", "--tile-pad": "16px 20px", "--edge": "4px",
    "--display": "\"Fredoka\", \"Nunito\", sans-serif", "--body": "\"Nunito\", system-ui, sans-serif",
    "--text-size": "15px", "--body-weight": "500", "--head-weight": "600",
    "--term-bg": "#0b0714", "--term-fg": "#f7eef8",
}

# Harvested from PuffPAIBuildBot@774e27a4:dashboard/css/_theme.css (adapted: the night and day palettes; lane contrast,
# D87, made the clouds fainter, night panels smoked and the text colours read at 4.5:1, tests/test_contrast.py)
_PUFF_NIGHT = {
    "--bg": "#0B0714", "--bg2": "#150C24",
    "--glow1": "rgba(240, 111, 208, 0.12)", "--glow2": "rgba(98, 216, 240, 0.10)",
    "--card": "rgba(11, 7, 20, 0.6)", "--card2": "rgba(11, 7, 20, 0.42)",
    "--line": "rgba(255, 255, 255, 0.10)",
    "--text": "#F7EEF8", "--text2": "#CAC2CD", "--text3": "#B0A9B4",
    "--pink": "#F286D8", "--magenta": "#C64BD8", "--sky": "#62D8F0", "--mint": "#6EE7B7",
    "--lemon": "#FCD97A", "--lav": "#A88BE8",
    "--railBg": "rgba(11, 7, 20, 0.54)", "--chip": "rgba(0, 0, 0, 0.22)",
    "--shadow": "0 18px 50px rgba(0, 0, 0, 0.45)",
    "--imgTile": "0.16", "--imgPage": "0.16", "--img2": "0.20",
    "--red": "#F19090", "--glow": "rgba(240, 111, 208, 0.20)", "--on-accent": "#0B0714",
    "--on-primary": "#0B0714", "--deep": "rgba(21, 12, 36, 0.92)", "--stars": "1",
    **_V1_SHAPE,
}
_PUFF_DAY = {
    "--bg": "#CFE9F9", "--bg2": "#FBDDEE",
    "--glow1": "rgba(255, 150, 215, 0.40)", "--glow2": "rgba(140, 205, 245, 0.40)",
    "--card": "rgba(255, 255, 255, 0.82)", "--card2": "rgba(255, 255, 255, 0.62)",
    "--line": "rgba(90, 60, 110, 0.12)",
    "--text": "#2E2140", "--text2": "#413551", "--text3": "#524760",
    "--pink": "#A42E88", "--magenta": "#8E2AA8", "--sky": "#0F516B", "--mint": "#0F6D57",
    "--lemon": "#664713", "--lav": "#6E4FC4",
    "--railBg": "rgba(255, 255, 255, 0.76)", "--chip": "rgba(255, 255, 255, 0.6)",
    "--shadow": "0 18px 46px rgba(120, 90, 160, 0.16)",
    "--imgTile": "0.18", "--imgPage": "0.22", "--img2": "0.42",
    "--red": "#B72727", "--glow": "rgba(120, 90, 160, 0.18)", "--on-accent": "#ffffff",
    "--on-primary": "#ffffff", "--deep": "rgba(255, 255, 255, 0.66)", "--stars": "0",
    **_V1_SHAPE,
}
# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/ui/theme.py (adapted: v1's light and dark palettes,
# put on the Desktop's token names; the accent pair is its blue and navy, the glows its aqua and green; D87 as Puff)
_AERO_DAY = {
    "--bg": "#D4EFF8", "--bg2": "#EAF7FC",
    "--glow1": "rgba(0, 180, 216, 0.25)", "--glow2": "rgba(82, 183, 136, 0.22)",
    "--card": "rgba(255, 255, 255, 0.82)", "--card2": "rgba(255, 255, 255, 0.62)",
    "--line": "rgba(0, 95, 138, 0.14)",
    "--text": "#0C2F44", "--text2": "#1F4053", "--text3": "#355264",
    "--pink": "#00688A", "--magenta": "#005F8A", "--sky": "#005668", "--mint": "#276D53",
    "--lemon": "#6D450C", "--lav": "#6C63B0",
    "--railBg": "rgba(255, 255, 255, 0.76)", "--chip": "rgba(255, 255, 255, 0.6)",
    "--shadow": "0 18px 46px rgba(0, 95, 138, 0.16)",
    "--imgTile": "0.18", "--imgPage": "0.22", "--img2": "0.42",
    "--red": "#B92828", "--glow": "rgba(0, 150, 199, 0.18)", "--on-accent": "#ffffff",
    "--on-primary": "#ffffff", "--deep": "rgba(255, 255, 255, 0.66)", "--stars": "0",
    **_V1_SHAPE,
}
_AERO_NIGHT = {
    "--bg": "#1A1A2E", "--bg2": "#202040",
    "--glow1": "rgba(0, 180, 216, 0.12)", "--glow2": "rgba(82, 183, 136, 0.10)",
    "--card": "rgba(26, 26, 46, 0.6)", "--card2": "rgba(26, 26, 46, 0.42)",
    "--line": "rgba(255, 255, 255, 0.10)",
    "--text": "#E8E8F0", "--text2": "#D8D8E0", "--text3": "#BDBDC7",
    "--pink": "#55CEE6", "--magenta": "#0096C7", "--sky": "#90E0EF", "--mint": "#95D5B2",
    "--lemon": "#EDB462", "--lav": "#A78BCC",
    "--railBg": "rgba(26, 26, 46, 0.54)", "--chip": "rgba(0, 0, 0, 0.22)",
    "--shadow": "0 18px 50px rgba(0, 0, 0, 0.45)",
    "--imgTile": "0.16", "--imgPage": "0.16", "--img2": "0.20",
    "--red": "#F5ACAC", "--glow": "rgba(72, 202, 228, 0.20)", "--on-accent": "#1A1A2E",
    "--on-primary": "#1A1A2E", "--deep": "rgba(32, 32, 64, 0.92)", "--stars": "1",
    **_V1_SHAPE,
}

THEMES: list[dict] = [
    {"id": "clean", "name": "Clean", "mode": "both",
     "line": "Square corners, flat grey and one blue accent, mono headings. Dark at night, light by day.",
     "tokens": _CLEAN_NIGHT, "day_tokens": _CLEAN_DAY},
    {"id": "puff", "name": "Puff", "mode": "both",
     "line": "Near-black plum at night, cotton-candy pink and sky by day. It switches by itself.",
     "tokens": _PUFF_NIGHT, "day_tokens": _PUFF_DAY},
    {"id": "puff-night", "name": "Puff Night", "mode": "night",
     "line": "Near-black plum with pink and sky glows, day and night.", "tokens": _PUFF_NIGHT},
    {"id": "puff-day", "name": "Puff Day", "mode": "day",
     "line": "Cotton-candy pink and sky blue, day and night.", "tokens": _PUFF_DAY},
    {"id": "aero", "name": "Aero", "mode": "day",
     "line": "Sky blue and aqua glass with green accents.", "tokens": _AERO_DAY},
    {"id": "aero-night", "name": "Aero Night", "mode": "night",
     "line": "Deep navy with aqua and green accents.", "tokens": _AERO_NIGHT},
]


def themes(state: Path | str | None = None, registry: Any = None) -> list[dict]:
    """Every look, as ``GET /v1/settings/themes`` answers it (copies: a caller can't change them): the
    built-in ones, then the installed look packages (D76) when *state* and *registry* are given."""
    from . import looks
    out = []
    for t in THEMES:
        row = {k: (dict(v) if isinstance(v, dict) else v) for k, v in t.items()}
        out.append(row)
    return out + looks.installed(state, registry, taken={t["id"] for t in THEMES})


def theme_ids(state: Path | str | None = None, registry: Any = None) -> list[str]:
    return [t["id"] for t in themes(state, registry)]


def theme_name(theme_id: str, state: Path | str | None = None, registry: Any = None) -> str:
    return next((t["name"] for t in themes(state, registry) if t["id"] == theme_id), theme_id)


def check_name(raw: Any) -> str:
    """Her bot's name, trimmed, or ValueError with the plain reason. 1 to 40 characters of plain text: no
    control or hidden characters, no markup, no link, nothing that reads like orders to an AI."""
    if not isinstance(raw, str):
        raise ValueError("The name is plain text, like Puff.")
    from .zone3.text_rules import has_hidden, owner_text_flag
    if has_hidden(raw, emoji=True):     # the one list, shared with the persona and You checks
        raise ValueError("The name can't have hidden or control characters. Type it again.")
    name = " ".join(raw.split())
    if not name:
        raise ValueError("Type a name for your bot, like Puff.")
    if not any(unicodedata.category(ch)[0] in "LNSP" for ch in name):
        raise ValueError("The name needs a letter, a number or an emoji you can see. Type it again.")
    if len(name) > NAME_MAX:
        raise ValueError(f"A name is at most {NAME_MAX} characters. Pick a shorter one.")
    why = owner_text_flag(name.replace(chr(0x200D), ""))     # a joiner here is between two emoji
    if why:
        raise ValueError(f"That name can't be used: {why}. Pick a plain name.")
    return name


def check_theme(raw: Any, state: Path | str | None = None, registry: Any = None) -> str:
    ids = theme_ids(state, registry)
    if not isinstance(raw, str) or raw not in ids:
        raise ValueError(f"That is not a look this bot has. Pick one of: {', '.join(ids)}.")
    return raw


def _path(state: Path | str) -> Path:
    return Path(state).joinpath(*FILE)


def read(state: Path | str, registry: Any = None) -> dict:
    """``{"name", "theme"}``: what she picked, else the defaults. A broken file or value reads as the default,
    and so does a look package that is no longer installed (with *registry*; without it, only built-ins).
    A bot whose setup finished with no look saved reads as Puff (:data:`SET_UP_THEME`), the look it had
    before D90 made Clean the default."""
    data = jsonfile.read(_path(state), {})
    data = data if isinstance(data, dict) else {}
    try:
        name = check_name(data.get("name"))
    except ValueError:
        name = DEFAULT_NAME
    theme = data.get("theme")
    if theme not in theme_ids(state, registry):
        theme = SET_UP_THEME if "theme" not in data and _set_up(state) else DEFAULT_THEME
    return {"name": name, "theme": theme}


def _set_up(state: Path | str) -> bool:
    wizard = jsonfile.read(Path(state) / "settings" / "wizard.json", {})
    return isinstance(wizard, dict) and bool(wizard.get("complete"))


def save(state: Path | str, name: Any = None, theme: Any = None, by: str = "owner", registry: Any = None) -> dict:
    """Change the name, the look, or both (None leaves one as it is). ValueError with the plain reason; nothing
    is saved then. Returns the new ``{"name", "theme"}``. With *registry*, an installed look package counts."""
    now = read(state, registry)
    if name is not None:
        now["name"] = check_name(name)
    if theme is not None:
        now["theme"] = check_theme(theme, state, registry)
    jsonfile.write(_path(state), {**now, "by": str(by or "owner")[:64]})
    return now
