"""Outside MCP tool servers the owner adds by their web address (lane api-tools, D137; port of v1's "Add my own tool").

She gives a name and a web address (and a password, on the Developer page only). BuildBot connects first and keeps it only
if it answers. Its tools then show up for the brain as ``<id>.<tool>``, like a package's, and every call goes through
Drawbridge's review (``wiring.tool_review``) the same way: a read-only tool runs, anything else asks.

- **The record**: ``<state>/settings/tool_servers.json``, written only here. ``{id, name, url, kind: "http", has_password,
  added_at, added_by: "owner" | "chat", tools: [{name, description}], state: "ready" | "waiting"}``. The id is a slug of
  the name (v1 ``mcp_client.slug``: lowercase letters, digits and underscores, a letter first, at most 40).
- **A password** is kept in the vault as ``tool_server.<id>`` (the Archivist), never in the record, a log or a reply. It
  goes out as ``Authorization: Bearer <password>`` (as typed when it starts with "bearer "). With the vault not open it
  can't be kept, so nothing is added. Removing the server takes it out of the vault.
- **Where**: web addresses only (``http://`` or ``https://``), no user name or password inside, no ``?`` or ``#`` part.
  On this computer (127.0.0.1, localhost, ::1) it connects at once, and one that does not answer is not kept (v1).
  Anywhere else it is saved as "waiting" and the Bridge's own address card (D110) is raised for that one host and port,
  for the package id ``outside-<id>``. Nothing here grants: only her Yes on that card does. After it, refresh connects.
  Every connect and call off this computer asks the Bridge first (``Bridge.request``), so a Yes taken back stops it.
- **Left out**: a command that starts a server on this computer (v1's other form) belongs to the own-programs lane (D16).
  v1's older ``/sse`` servers are not supported: the Core's MCP client speaks streamable HTTP only.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from . import jsonfile
from .zone2 import mcp

logger = logging.getLogger(__name__)
FILE = "tool_servers.json"
PREFIX = "outside-"                    # its package id on the Bridge and for Drawbridge's scope
ORIGIN = "adopted:outside"             # Drawbridge's rule: a tool with no readOnlyHint counts as changing
VAULT_PREFIX = "tool_server."
BORROWER = "core:tool-servers"
PURPOSE = "tool_server"
MAX_TYPED = 300
MAX_NAME = 60
SECRET_ARGS = ("token", "password", "secret", "key")
ID_RE = re.compile(r"[a-z][a-z0-9_]{0,39}")
_LOCK = threading.RLock()

WEB_ONLY_LINE = ("Give a web address that starts with http:// or https://. A command that starts a tool server is not "
                 "taken here yet.")
PLAIN_LINE = "Give the tool server's web address on one line, like http://127.0.0.1:8000/mcp, with no spaces."
IN_ADDRESS_LINE = "Put a password in the password box, not in the address. Nothing was kept."
NO_QUERY_LINE = "Give the address with no ? or # part. Nothing was kept."
KEYISH_LINE = "That address looks like it holds a key or a token. Put it in the password box instead. Nothing was kept."
BAD_PORT_LINE = "The port is a number from 1 to 65535."
SSE_LINE = "That is the older kind of tool server (an address ending in /sse). BuildBot talks to the newer kind only."
HERE_SPELLING_LINE = "For a tool server on this computer, use 127.0.0.1 or localhost."
NAME_LINE = "Give the tool server a short name, like my_tools."
SAME_LINE = "You already added a tool server called {id}. Remove it first, or pick another name."
TAKEN_LINE = "Something installed on this bot already uses the name {id}. Pick another name."
NO_ROW_LINE = "There is no tool server by that name."
QUERY_LINE = "A password is never sent in the address. Nothing was kept."
BAD_PASSWORD_LINE = "That password can't be sent: paste it on one line, with no spaces inside. Nothing was kept."
VAULT_SETUP_LINE = "Set up your vault first, so the password has a safe home. Nothing was kept."
VAULT_LOCKED_LINE = "Unlock your vault first, so the password has a safe home. Nothing was kept."
VAULT_REMOVE_LINE = "Unlock your vault first, so its password goes too. Nothing was removed."
VAULT_USE_LINE = "Unlock your vault so BuildBot can use the password for {name}."
WAITING_LINE = "Waiting for your Yes on the Bridge page for {place}. Nothing connects before that."
CLOSED_LINE = "The Bridge is closed, so {name} can't be reached. Open it on the Bridge page."
NO_CARD_LINE = "No Yes card could be raised right now, so nothing was kept."
NOT_KEPT = " Nothing was kept."
PASSWORD_IN_CHAT = ("A password never goes in chat. If it needs one, add it on the Developer page, under Brain, Add my "
                    "own tool, which takes its password.")


class Refused(ValueError):
    """One plain line, and the HTTP status for it. Never repeats what was typed."""

    def __init__(self, line: str, status: int = 400) -> None:
        super().__init__(line)
        self.line = line
        self.status = status


@dataclass(frozen=True)
class Place:
    url: str
    host: str
    port: int
    scheme: str
    where: str                         # here | lan | internet
    address: Any = None                # zone3.addresses.Address, off this computer

    @property
    def said(self) -> str:
        return f"[{self.host}]:{self.port}" if ":" in self.host else f"{self.host}:{self.port}"


def slug(text: Any) -> str:
    """v1 ``mcp_client.slug``: "My Printer" -> ``my_printer``; a letter first; at most 40. Empty when nothing is left."""
    out = re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")
    if out and not out[0].isalpha():
        out = "t_" + out
    return out[:40].rstrip("_")


def _keyish(text: str) -> bool:
    try:
        from .zone1 import secret_detector
        return bool(secret_detector.find(text))
    except Exception:  # noqa: BLE001 - fail closed: treated as a key
        return True


def parse(text: Any) -> Place:
    """The one web address in what was typed, or :class:`Refused`."""
    from .zone3 import addresses
    if not isinstance(text, str):
        raise Refused(PLAIN_LINE)
    text = text.strip()
    if text and not re.match(r"(?i)https?://", text):
        raise Refused(WEB_ONLY_LINE)
    if not text or len(text) > MAX_TYPED or not text.isascii() or not text.isprintable() or " " in text:
        raise Refused(PLAIN_LINE)
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise Refused(BAD_PORT_LINE if re.match(r"(?i)https?://[^/]*:", text) else PLAIN_LINE) from None
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise Refused(IN_ADDRESS_LINE)
    if parts.query or parts.fragment or "?" in text or "#" in text:
        raise Refused(NO_QUERY_LINE)
    host = (parts.hostname or "").lower()
    if not host:
        raise Refused(PLAIN_LINE)
    if _keyish(text):
        raise Refused(KEYISH_LINE)
    if parts.path.rstrip("/").lower().endswith("/sse"):
        raise Refused(SSE_LINE)
    scheme = parts.scheme.lower()
    port = port or (443 if scheme == "https" else 80)
    if host in mcp._LOOPBACK:                         # the MCP client's own list (zone2.mcp)
        return Place(text, host, port, scheme, "here")
    said = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    try:
        address = addresses.parse(said)
    except addresses.NotNeeded:
        raise Refused(HERE_SPELLING_LINE) from None
    except addresses.Refused as exc:
        raise Refused(exc.line) from None
    return Place(text, address.host, address.port, scheme, address.where, address)


# -- the record -----------------------------------------------------------------------------------------------------

def path(state: Path | str) -> Path:
    return Path(state) / "settings" / FILE


def _tools(listed: Any) -> list[dict]:
    out = []
    for t in listed if isinstance(listed, list) else []:
        if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]:
            out.append({"name": t["name"][:128], "description": str(t.get("description") or "")[:300]})
    return out[:mcp.MAX_TOOLS_PER_SERVER]


def _good(sid: Any, raw: Any) -> Optional[dict]:
    """A row as this code wrote it, or None (a row edited by hand into something else is not there)."""
    if not isinstance(sid, str) or not ID_RE.fullmatch(sid) or not isinstance(raw, dict):
        return None
    try:
        place = parse(raw.get("url"))
    except Refused:
        return None
    if raw.get("state") not in ("ready", "waiting"):
        return None
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else sid
    return {"id": sid, "name": name[:MAX_NAME], "url": place.url, "kind": "http", "has_password": raw.get("has_password") is True,
            "added_at": str(raw.get("added_at") or ""), "added_by": "chat" if raw.get("added_by") == "chat" else "owner",
            "tools": _tools(raw.get("tools")), "state": raw["state"]}


def rows(state: Path | str) -> dict[str, dict]:
    raw = jsonfile.read(path(state), {})
    servers = raw.get("servers") if isinstance(raw, dict) else None
    out = {}
    for sid, rec in (servers if isinstance(servers, dict) else {}).items():
        good = _good(sid, rec)
        if good is None:
            logger.warning("a tool server row failed its checks and is ignored")
            continue
        out[sid] = good
    return out


def _write(state: Path | str, kept: dict[str, dict]) -> None:
    jsonfile.write(path(state), {"servers": kept})


def _save(state: Path | str, rec: dict) -> None:
    with _LOCK:
        kept = rows(state)
        kept[rec["id"]] = rec
        _write(state, kept)


def _drop(state: Path | str, sid: str) -> None:
    with _LOCK:
        kept = rows(state)
        kept.pop(sid, None)
        _write(state, kept)


def manifest_for(state: Path | str, package: Any) -> Optional[dict]:
    """The manifest Drawbridge's scope reads for ``outside-<id>`` (``wiring.refresh_scope``): made here, never a file."""
    if not isinstance(package, str) or not package.startswith(PREFIX):
        return None
    rec = rows(state).get(package[len(PREFIX):])
    if rec is None:
        return None
    return {"id": package, "name": rec["name"], "kind": "tool",
            "abilities": {"mcp": {"transport": "http", "url": rec["url"]}}}


def _place_words(place: Place) -> str:
    return {"here": "this computer", "lan": "home network", "internet": "internet"}[place.where]


def public(rec: dict) -> dict:
    """A row for the Desktop and chat. Never a password: only whether one is kept."""
    place = parse(rec["url"])
    if rec["state"] == "ready":
        n = len(rec["tools"])
        line = f"Ready. {n} tool{'s' if n != 1 else ''} for the brain."
    else:
        line = WAITING_LINE.format(place=place.said)
    return {"id": rec["id"], "name": rec["name"], "url": rec["url"], "kind": "http", "address": place.said,
            "where": place.where, "where_said": _place_words(place), "has_password": rec["has_password"],
            "added_at": rec["added_at"], "added_by": rec["added_by"], "tools": rec["tools"], "tool_count": len(rec["tools"]),
            "state": rec["state"], "line": line}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _label(name: Any, sid: str) -> str:
    said = " ".join(str(name or "").split())[:MAX_NAME]
    return said if said and said.isprintable() else sid


class ToolServers:
    """The one place that adds, lists, refreshes and removes them: the routes, chat and the brain all use it.

    ``zone()``: the Core's Zone 3 (``zone3.service.for_app``), asked only when it is needed."""

    def __init__(self, core: Any, zone: Optional[Callable[[], Any]] = None) -> None:
        self.core = core
        self._zone = zone

    @property
    def state(self) -> Path:
        return Path(self.core.state)

    def zone(self) -> Any:
        try:
            return self._zone() if self._zone is not None else getattr(self.core, "zone3", None)
        except Exception:  # noqa: BLE001 - no Zone 3: nothing off this computer
            return None

    def archivist(self) -> Any:
        from .zone1 import archivist
        return archivist.for_state(self.core.state, self.core.bus)

    def _vault_state(self) -> str:
        try:
            return str(self.archivist().vault.status().get("state") or "")
        except Exception:  # noqa: BLE001 - can't tell
            return ""

    # -- the brain ---------------------------------------------------------------------------------------------
    def ready(self) -> list[dict]:
        return [r for r in rows(self.state).values() if r["state"] == "ready"]

    def target(self, sid: str) -> mcp.Target:
        """Its MCP target, with its password read from the vault now. Raises :class:`~.zone2.mcp.McpError`."""
        rec = rows(self.state).get(sid)
        if rec is None:
            raise mcp.McpError(NO_ROW_LINE, "none")
        return self._target(rec, parse(rec["url"]), self._password(rec) if rec["has_password"] else None)

    def _target(self, rec: dict, place: Place, password: Optional[str]) -> mcp.Target:
        return mcp.Target(package=PREFIX + rec["id"], kind="http", url=rec["url"], token=password, outside=True,
                          name=rec["name"], allowed=None if place.where == "here" else self._may_reach(rec, place))

    def _may_reach(self, rec: dict, place: Place) -> Callable[[], str]:
        """The Bridge's own answer for this one host and port, asked at each connect: "" when it may go now."""
        def check() -> str:
            z = self.zone()
            if z is None:
                return WAITING_LINE.format(place=place.said)
            try:
                if z.bridge.request("pkg:" + PREFIX + rec["id"], place.host, port=place.port, scheme=place.scheme).allowed:
                    return ""
            except Exception:  # noqa: BLE001 - no answer is a no
                logger.exception("the Bridge could not answer for a tool server")
            return CLOSED_LINE.format(name=rec["name"]) if _closed(z) else WAITING_LINE.format(place=place.said)
        return check

    def _password(self, rec: dict) -> str:
        """Its password from the vault, through a short lease: the bytes stay in this process, never in a reply."""
        a = self.archivist()
        if not a.is_open():
            raise mcp.McpError(VAULT_USE_LINE.format(name=rec["name"]), "locked")
        handle = None
        try:
            handle = a.lend(VAULT_PREFIX + rec["id"], BORROWER, PURPOSE, ttl_s=60)
            value = a.use(handle, borrower=BORROWER, purpose=PURPOSE)
        except Exception:  # noqa: BLE001 - not lent: no call
            raise mcp.McpError(f"The password for {rec['name']} could not be read from your vault.", "locked") from None
        finally:
            if handle:
                a.end(handle, "used")
        return bytes(value).decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else str(value)

    def scope(self, sid: str) -> None:
        """Drawbridge's scope for its tools, from the record (``wiring.refresh_scope``)."""
        from . import wiring
        wiring.refresh_scope(self.core, PREFIX + sid)

    def _drop_scope(self, sid: str) -> None:
        db = getattr(getattr(self.core, "packages", None), "drawbridge", None)
        if db is not None and callable(getattr(db, "drop_scope", None)):
            db.drop_scope(PREFIX + sid)

    # -- add, refresh, remove: (status, reply) ------------------------------------------------------------------
    def listed(self) -> list[dict]:
        return [public(r) for r in rows(self.state).values()]

    def add(self, name: Any, address: Any, password: Any = None, *, by: str = "owner") -> tuple[int, dict]:
        try:
            place = parse(address)
        except Refused as exc:
            return exc.status, _refusal(exc.line)
        sid = slug(name) or slug(place.host)
        if not ID_RE.fullmatch(sid or ""):
            return 400, _refusal(NAME_LINE)
        if sid in rows(self.state):
            return 409, _refusal(SAME_LINE.format(id=sid))
        registry = getattr(self.core, "registry", None)
        if registry is not None and (registry.get(sid) or registry.get(PREFIX + sid)):
            return 409, _refusal(TAKEN_LINE.format(id=sid))
        if password is not None and not isinstance(password, str):
            return 400, _refusal(BAD_PASSWORD_LINE)
        password = (password or "").strip() or None
        if password is not None:
            if mcp.outside_auth(password) is None:
                return 400, _refusal(BAD_PASSWORD_LINE)
            vault = self._vault_state()
            if vault != "open":
                return 409, _refusal(VAULT_LOCKED_LINE if vault == "locked" else VAULT_SETUP_LINE)
        label = _label(name, sid)
        rec = {"id": sid, "name": label, "url": place.url, "kind": "http", "has_password": password is not None,
               "added_at": _now(), "added_by": "chat" if by == "chat" else "owner", "tools": [], "state": "waiting"}
        if place.where == "here":
            try:                                   # connects first: one that does not answer is not kept (v1)
                tools = mcp.list_tools(self._target(rec, place, password))
            except mcp.McpError as exc:
                return 502, _refusal(exc.message.rstrip(".") + "." + NOT_KEPT)
            rec.update(tools=_tools(tools), state="ready")
            refused = self._keep(sid, password)
            if refused:
                return refused
            _save(self.state, rec)
            self.scope(sid)
            n = len(rec["tools"])
            return 200, {"server": public(rec), "state": "ready",
                         "line": f"{label} is added. It has {n} tool{'s' if n != 1 else ''} your bot can use in chat."}
        got = self._ask(rec, place)
        if "error" in got:
            return got.pop("status"), got
        refused = self._keep(sid, password)
        if refused:
            self._take_back(sid)
            return refused
        _save(self.state, rec)
        if got.get("state") == "allowed":
            return self.refresh(sid)
        return 202, {"server": public(rec), **got}

    def _keep(self, sid: str, password: Optional[str]) -> Optional[tuple[int, dict]]:
        if password is None:
            return None
        from .zone1.vault import VaultError
        try:
            got = self.archivist().put_secret(VAULT_PREFIX + sid, password, label="secret.api_key")
        except VaultError as exc:
            return exc.status, _refusal(str(exc.message).rstrip(".") + "." + NOT_KEPT)
        if not got.get("saved"):                   # sealed in the inbox: it would not work now, so it is not added
            try:
                self.archivist().delete_secret(VAULT_PREFIX + sid)
            except Exception:  # noqa: BLE001
                pass
            return 409, _refusal(VAULT_LOCKED_LINE)
        return None

    def _ask(self, rec: dict, place: Place) -> dict:
        """The Bridge's address card for this one host and port (D110), as her own form raises it. Never grants."""
        from . import anchor as _anchor
        from .consent import ConsentError, card_view
        from .zone3 import addresses
        z = self.zone()
        if z is None:
            return {"status": 503, **_refusal(NO_CARD_LINE)}
        try:
            got = addresses.ask(z.consents, z.bridge.policy, PREFIX + rec["id"], rec["name"], place.address,
                                closed=_closed(z), by_package=False)
        except ConsentError as exc:
            said = str(getattr(exc, "message", "") or exc)
            return {"status": getattr(exc, "status", 503), **_refusal(said)}
        except (_anchor.AnchorUnavailable, addresses.Refused):
            return {"status": 503, **_refusal(NO_CARD_LINE)}
        line = WAITING_LINE.format(place=place.said)
        if got["state"] == "allowed":
            return {"state": "allowed", "line": ""}
        card = got.get("record")
        if card is None:                            # her Yes stands and its grant can't be read now: no second card
            return {"state": "waiting", "line": got.get("line") or line}
        seen = card_view(card.get("card") if isinstance(card.get("card"), dict) else {}, bool(card.get("floor")))
        return {"state": "waiting", "consent_id": card["consent_id"], "card_sha": card.get("card_sha"),
                "title": seen["title"], "lines": seen["lines"], "line": line}

    def _take_back(self, sid: str) -> None:
        from .zone3 import addresses
        z = self.zone()
        if z is None:
            return
        try:
            addresses.take_back_package(z.consents, PREFIX + sid, z.bridge.drop_consent)
        except Exception:  # noqa: BLE001 - logged; a revoked Yes stops the Bridge on its own
            logger.exception("a tool server's address was not taken back")

    def refresh(self, sid: str) -> tuple[int, dict]:
        """Ask it for its tools again. Off this computer: only after her Yes (else its card, again if it is gone)."""
        rec = rows(self.state).get(str(sid or ""))
        if rec is None:
            return 404, _refusal(NO_ROW_LINE)
        place = parse(rec["url"])
        if place.where != "here":
            got = self._ask(rec, place)
            if "error" in got:
                return got.pop("status"), got
            if got.get("state") != "allowed":
                return 202, {"server": public(rec), **got}
        try:
            target = self.target(rec["id"])
            tools = mcp.list_tools(target)
        except mcp.McpError as exc:
            code = {"locked": 423, "bridge": 409}.get(exc.code, 502)
            return code, {**_refusal(exc.message), "server": public(rec)}
        rec.update(tools=_tools(tools), state="ready")
        _save(self.state, rec)
        self.scope(rec["id"])
        n = len(rec["tools"])
        return 200, {"server": public(rec), "state": "ready",
                     "line": f"{rec['name']} answered. It has {n} tool{'s' if n != 1 else ''} your bot can use in chat."}

    def remove(self, sid: str) -> tuple[int, dict]:
        """Its record, its password in the vault, its address Yes and grant, and Drawbridge's scope: all go."""
        rec = rows(self.state).get(str(sid or ""))
        if rec is None:
            return 404, _refusal(NO_ROW_LINE)
        if rec["has_password"]:
            from .zone1.vault import VaultError
            a = self.archivist()
            if not a.is_open():
                return 423, _refusal(VAULT_REMOVE_LINE)
            try:
                a.delete_secret(VAULT_PREFIX + rec["id"])
            except VaultError as exc:
                return exc.status, _refusal(str(exc.message))
        self._take_back(rec["id"])
        _drop(self.state, rec["id"])
        self._drop_scope(rec["id"])
        tail = " Its password is out of your vault." if rec["has_password"] else ""
        return 200, {"deleted": True, "line": f"{rec['name']} is removed. Its tools are gone from chat.{tail}"}


def _refusal(line: str) -> dict:
    return {"error": line, "line": line}


def _closed(z: Any) -> bool:
    from . import anchor as _anchor
    try:
        return z.bridge.view({}).get("setting") != "open"
    except (_anchor.AnchorUnavailable, OSError):
        return True


__all__ = ["ORIGIN", "PREFIX", "PASSWORD_IN_CHAT", "Place", "QUERY_LINE", "Refused", "SECRET_ARGS", "ToolServers",
           "VAULT_PREFIX", "manifest_for", "parse", "public", "rows", "slug"]
