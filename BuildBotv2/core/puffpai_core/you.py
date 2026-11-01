r"""The owner's "You" settings (lane I12): ``{name, call_me, about, timezone}``.

Personal data goes to the vault (00 "One data folder": personal data found goes to the vault; Z1C: P2 and up
are held there). So the values are ONE held vault item (``Archivist.hold``, label :data:`LABEL`, an unknown
label, so P3 and held: unsure rounds up), and ``settings\you.json`` keeps only its handle ref, the way the
chat store keeps handles and never values. Saving while the vault is locked seals it into the vault inbox,
filed at the next unlock. Reading needs the vault open; while it is locked the answer is
:class:`YouLocked` (423), never an empty form (Z1V F8: locked means refuse, never empty).

The chat reads it only while the vault is open, only for the brain on this computer (D25), and only when the
caller is her paired device: ``call_me`` in greetings, ``about`` as her own words (:func:`for_brain`). Both
pass Drawbridge's screen for owner text first; a flagged value is kept but never reaches the brain, and the
replies say why (``flag``).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import jsonfile
from .zone2.persona import flag as screen, hidden_char

LABEL = "owner.you"
FIELDS = ("name", "call_me", "about", "timezone")
EMPTY = {"name": "", "call_me": "", "about": "", "timezone": ""}
NAME_MAX = 40
ABOUT_MAX = 1000

#: The IANA areas (tzdata's top level), for the shape check when this computer has no time zone database.
_AREAS = ("Africa", "America", "Antarctica", "Arctic", "Asia", "Atlantic", "Australia", "Europe", "Indian",
          "Pacific", "Etc")
_TZ_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-]{0,29}$")


class YouError(ValueError):
    status = 400


class YouLocked(YouError):
    status = 423

    def __init__(self) -> None:
        super().__init__("Your vault is locked. Unlock it to see what you told your bot about you.")


class NoVault(YouError):
    status = 409


def path(state: Path | str) -> Path:
    return Path(state) / "settings" / "you.json"


def _archivist(state: Path | str, bus: Any = None) -> Any:
    from . import seams
    return seams.archivist(state, bus)


def valid_timezone(tz: str) -> bool:
    """A real IANA name. With a time zone database here, by its list; without one (Windows has none for
    Python unless ``tzdata`` is installed), by the IANA shape: ``UTC``, or an IANA area and a place."""
    if tz in ("UTC", "Etc/UTC", "GMT"):
        return True
    try:
        import zoneinfo
        known = zoneinfo.available_timezones()
    except Exception:  # noqa: BLE001 - no database: the shape check below
        known = set()
    if known:
        return tz in known
    parts = tz.split("/")
    return 2 <= len(parts) <= 3 and parts[0] in _AREAS and all(_TZ_PART.match(p) for p in parts[1:])


def _plain(value: Any, field: str, *, lo: int, hi: int, lines: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise YouError(f"{field} is text.")
    if any(hidden_char(ch, lines=lines) for ch in value):
        raise YouError(f"{field} has hidden or control characters. Type it again as plain text.")
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip() if lines else " ".join(value.split())
    if not lo <= len(value) <= hi:
        raise YouError(f"{field} is {lo} to {hi} characters." if lo else f"{field} is at most {hi} characters.")
    return value


def check(body: Any) -> dict:
    """Her four answers, checked. Raises :class:`YouError` with the plain reason."""
    if not isinstance(body, dict):
        raise YouError('Send {"name", "call_me", "about", "timezone"}.')
    unknown = set(body) - set(FIELDS)
    if unknown:
        raise YouError(f"These are not kept: {', '.join(sorted(unknown))}.")
    out = {"name": _plain(body.get("name"), "name", lo=1, hi=NAME_MAX),
           "call_me": _plain(body.get("call_me"), "call_me", lo=1, hi=NAME_MAX),
           "about": _plain(body.get("about"), "about", lo=0, hi=ABOUT_MAX, lines=True),
           "timezone": _plain(body.get("timezone"), "timezone", lo=0, hi=64)}
    if out["timezone"] and not valid_timezone(out["timezone"]):
        raise YouError("timezone is a time zone name like America/Chicago or Europe/Paris, or empty.")
    return out


def _ref(state: Path | str) -> str:
    got = jsonfile.read(path(state), {})
    return str(got.get("ref") or "") if isinstance(got, dict) else ""


def load(state: Path | str, archivist: Any = None, bus: Any = None) -> dict:
    """Her four answers (empty strings when she has not given them). Raises :class:`YouLocked` while the vault
    is locked and something is kept."""
    ref = _ref(state)
    if not ref:
        return dict(EMPTY)
    a = archivist or _archivist(state, bus)
    if a is None or not a.is_open():
        raise YouLocked()
    try:
        data = json.loads(a.resolve(ref))
    except Exception:  # noqa: BLE001 - not filed yet, or gone: nothing to show (said by the caller's form)
        return dict(EMPTY)
    return {k: str(data.get(k) or "") for k in FIELDS} if isinstance(data, dict) else dict(EMPTY)


#: The two answers that reach the brain, so the two that are screened.
SCREENED = ("call_me", "about")


def flags(answers: dict) -> dict:
    """``{call_me, about}``: "" when the value may reach the brain, else why not (``owner_text_flag``)."""
    return {k: (screen(answers.get(k)) if answers.get(k) else "") for k in SCREENED}


def view(state: Path | str, archivist: Any = None, bus: Any = None) -> dict:
    """What ``GET /v1/settings/you`` answers: her answers and ``flag``."""
    got = load(state, archivist, bus)
    return {**got, "flag": flags(got)}


def save(state: Path | str, body: Any, archivist: Any = None, bus: Any = None) -> dict:
    """Keep her answers in the vault (or its inbox while locked). ``{**answers, kept: vault | inbox}``."""
    clean = check(body)
    a = archivist or _archivist(state, bus)
    if a is None:
        raise NoVault("The vault is not here, so this can't be kept.")
    if not a.vault.is_set_up():
        raise NoVault("Your vault is not set up yet, so this can't be kept. Set up your vault first.")
    was_open = a.is_open()
    ref = a.hold(json.dumps(clean, ensure_ascii=False, sort_keys=True), LABEL)
    old = _ref(state)
    jsonfile.write(path(state), {"ref": ref, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if old and old != ref:
        try:
            a.expire(old, "replaced")
        except Exception:  # noqa: BLE001 - the old copy stays sealed in the vault; the new one is in use
            pass
    return {**clean, "kept": "vault" if was_open else "inbox", "flag": flags(clean)}


def for_brain(state: Path | str, local: bool) -> dict:
    """``{call_me, about}`` for the chat, only for the brain on this computer while the vault is open; else
    ``{}``. Never raises."""
    if not local or not _ref(state):
        return {}
    try:
        got = load(state)
    except Exception:  # noqa: BLE001 - locked or no vault: nothing of hers goes in
        return {}
    why = flags(got)
    return {k: got.get(k, "") for k in SCREENED if not why[k]}


__all__ = ["ABOUT_MAX", "EMPTY", "FIELDS", "LABEL", "NAME_MAX", "NoVault", "YouError", "YouLocked", "check",
           "flags", "for_brain", "load", "save", "valid_timezone", "view"]
