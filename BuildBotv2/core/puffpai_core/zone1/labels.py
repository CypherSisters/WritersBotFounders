"""The built-in labels: the floor pack and the default pack (Z1C sections 2 and 7).

The floor pack (keys, tokens, passwords, one-time codes, SSN, government id,
card, card security code, bank account, child data) is Core code: always
loaded, always run, never lowered (F1, F6, D31(g)). Installed label packs come
later (``zone1.label-pack``) and may only make labels stricter.

Effects: ``hold`` (value kept in the vault), ``index`` (open | marker | skip),
``cloud`` (strip | block_whole), ``share`` (allow | ask_each | never),
``keep`` (value | none: scrubbed, never stored). Unsure rounds up: an unknown
label is P3 with ``hold``.
"""

from __future__ import annotations

TIERS = ("P0", "P1", "P2", "P3", "P4")

_HELD = {"hold": True, "index": "marker", "cloud": "strip", "share": "ask_each", "keep": "value"}
_SECRET = {"hold": True, "index": "marker", "cloud": "strip", "share": "never", "keep": "value"}
_SCRUB = {"hold": False, "index": "marker", "cloud": "strip", "share": "never", "keep": "none"}

LABELS: dict[str, dict] = {
    # floor pack
    "secret.api_key": {"words": "an API key", "tier": "P4", "floor": True, "secret": True, "effects": _SECRET},
    "secret.private_key": {"words": "a private key", "tier": "P4", "floor": True, "secret": True, "effects": _SECRET},
    "secret.token": {"words": "a key or token", "tier": "P4", "floor": True, "secret": True, "effects": _SECRET},
    "auth.password": {"words": "a password", "tier": "P4", "floor": True, "secret": True, "effects": _SECRET},
    "secret.one_time_code": {"words": "a one-time code", "tier": "P4", "floor": True, "secret": True,
                             "effects": _SCRUB},
    "id.ssn": {"words": "a Social Security number", "tier": "P4", "floor": True, "effects": _HELD},
    "id.government": {"words": "an ID number", "tier": "P4", "floor": True, "effects": _HELD},
    "financial.card": {"words": "a card number", "tier": "P4", "floor": True, "effects": _HELD},
    "financial.card_cvv": {"words": "a card security code", "tier": "P4", "floor": True, "secret": True,
                           "effects": _SCRUB},
    "financial.bank_account": {"words": "a bank account number", "tier": "P4", "floor": True, "effects": _HELD},
    "child.any": {"words": "details about a child", "tier": "P4", "floor": True, "effects": _HELD},
    # default pack
    "health.condition": {"words": "health details", "tier": "P3", "effects": _HELD},
    "person.dob": {"words": "a birthday", "tier": "P3", "effects": _HELD},
    "place.address": {"words": "a home address", "tier": "P3", "effects": _HELD},
    "location.exact": {"words": "an exact location", "tier": "P3", "effects": _HELD},
    "person.school": {"words": "a school name", "tier": "P3", "effects": _HELD},
    "linkage.quasi_id": {"words": "details that together identify someone", "tier": "P3",
                         "effects": {**_HELD, "hold": False}},
    "person.email": {"words": "an email address", "tier": "P2", "effects": _HELD},
    "person.phone": {"words": "a phone number", "tier": "P2", "effects": _HELD},
    "vehicle.plate": {"words": "a licence plate", "tier": "P2", "effects": _HELD},
    "vehicle.vin": {"words": "a vehicle identification number", "tier": "P2", "effects": _HELD},
    "financial.last4": {"words": "the last digits of a card or account", "tier": "P2", "effects": _HELD},
    # Names stay open in the index and in text, as in v1 (Z1C 2, 7; ZONES_TOGETHER Q2).
    "person.name": {"words": "a full name", "tier": "P2",
                    "effects": {"hold": False, "index": "open", "cloud": "strip", "share": "ask_each", "keep": "value"}},
}

_UNKNOWN = {"words": "private details", "tier": "P3", "effects": _HELD}


def spec(label: str) -> dict:
    return LABELS.get(label, _UNKNOWN)


def tier_of(label: str) -> str:
    return spec(label)["tier"]


def words(label: str) -> str:
    return spec(label)["words"]


def is_secret(label: str) -> bool:
    """Keys, tokens, passwords: never to the brain, never leave as text (D31(b), X6, F4)."""
    return bool(spec(label).get("secret")) or label.startswith("secret.") or label == "auth.password"


def is_floor(label: str) -> bool:
    return bool(spec(label).get("floor"))


def effects(label: str) -> dict:
    return dict(spec(label)["effects"])


def top(tiers: list[str]) -> str:
    return max(tiers, key=TIERS.index) if tiers else "P0"
