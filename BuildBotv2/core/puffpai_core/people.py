"""The Core's shared people list (D12): ``{id, name, emoji, role: adult | child}``.

Kept in ``settings/people.json``. Apps never keep their own list. Child
profiles (what a child may use) come later; the people page is not in this
week's build, so the list is edited through ``PUT /v1/people``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import jsonfile

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
ROLES = ("adult", "child")


class PeopleError(ValueError):
    pass


def path_for(state: Path) -> Path:
    return Path(state) / "settings" / "people.json"


def load(state: Path) -> list[dict]:
    return jsonfile.read(path_for(state), [])


def check(people: Any) -> list[dict]:
    if not isinstance(people, list):
        raise PeopleError("Send the whole list: [{id, name, emoji, role}].")
    out, seen = [], set()
    for i, p in enumerate(people):
        where = f"Person {i + 1}"
        if not isinstance(p, dict):
            raise PeopleError(f"{where} is not an object.")
        unknown = set(p) - {"id", "name", "emoji", "role"}
        if unknown:
            raise PeopleError(f"{where} has fields the list does not keep: {', '.join(sorted(unknown))}.")
        pid, name, emoji, role = p.get("id"), p.get("name"), p.get("emoji", ""), p.get("role", "adult")
        if not isinstance(pid, str) or not _ID.match(pid):
            raise PeopleError(f"{where}: id is lower case letters, digits, _ or -, up to 40.")
        if pid in seen:
            raise PeopleError(f"{where}: the id {pid} is used twice.")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            raise PeopleError(f"{where}: a name of 1 to 60 characters.")
        if not isinstance(emoji, str) or len(emoji) > 8:
            raise PeopleError(f"{where}: emoji is one emoji (at most 8 characters).")
        if role not in ROLES:
            raise PeopleError(f"{where}: role is adult or child.")
        seen.add(pid)
        out.append({"id": pid, "name": name.strip(), "emoji": emoji, "role": role})
    return out


def save(state: Path, people: Any) -> list[dict]:
    clean = check(people)
    jsonfile.write(path_for(state), clean)
    return clean
