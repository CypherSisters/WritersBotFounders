"""Who is calling the Core API (contracts/openapi.yaml, "Who may call").

Five kinds of credential, all random tokens of which the Core keeps only a
SHA-256 hash, in ``<state>/tokens.db``:
- program key (``via: api_key``, label ``PROGRAM_LABEL`` + the owner's words): Home Assistant or another
  program the owner set up. It may call only routes with access ``public`` and ``program`` (the chat routes
  for programs, ``/openai/v1`` and ``/ollama/api``), never an owner route: no Yes card, setting, package or
  chat history. ``puffpai apikey new | list | revoke`` issues, lists and takes them back
  (``issue_program_key``, ``program_keys``, ``revoke_program_key``); it never issues or lists any other key;
- owner API key (``via: api_key``, any other label): the owner's own program. Nothing outside tests issues
  one today (``issue_owner_key``);
- paired device (``via: device``): an owner at a screen. Pairing itself is
  lane L5; this store issues and checks the tokens;
- package token (``via: package_token``): the package's own ``PUFFPAI_TOKEN``.
  It may call only brain, notify, theme, people (read only), its own Registry
  row, its own Checklist steps, its events, and asking for one address for itself
  (02 "How the Core runs a package").
- Toolsmith token (``via: toolsmith_token``, lane L6): Toolsmith's own
  process gets one per build on its stdin (09 section 3). It may call only
  routes with access ``toolsmith``, and only for that build; it can't call
  any owner, package or Core route. It is revoked when that process ends.

They arrive as ``Authorization: Bearer <token>`` or ``X-PuffPAI-Key: <token>``.
A device token may also come as the device cookie, on the Core's own address
only (D49), and only when ``Host`` is exactly ``puffpai.localhost:<port>`` (D79;
``api.app.on_desktop_host``): the pages address never reads the cookie, and a
state-changing request signed in by the cookie must pass ``cookie_request_ok``
(its Origin is the Desktop's own, and it carries ``X-PuffPAI: 1``). Several
cookies with the Core's names are each checked (``cookie_tokens``), never only
the first.

Each Core sets the cookie under a name with its own port (``cookie_name``,
D129), so two bots on one computer don't overwrite each other's sign-in in one
browser. The usual port keeps the plain ``puffpai_device``.

On the home network (D130, ``api.network``) a paired browser's cookie is named for that listener's port, and it is the
only credential taken there.

A browser's login has two halves (D79). Pairing issues the device token (the
cookie) and binds a second secret to it (``bind_device``; the Core keeps only
its SHA-256), returned once in the pairing reply's JSON body and kept by the
Desktop in its own origin's localStorage. A bound token counts only with its
second half in ``X-PuffPAI-Device`` (``bound_ok``), by cookie or as a bearer.
Cookies are not split by port, but localStorage is: a page on another port
of the same name can get the cookie, never the second half. That holds for
another bot too: every bot on this computer is sent every other bot's cookie,
and can read its value. It can't use it: the token is not in its own store, and
it can't get the second half.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PUBLIC = "public"
OWNER = "owner"
OWNER_DEVICE = "owner_device"
PACKAGE_OK = "package_ok"
OWN_ROW = "own_row"
CORE = "core"
TOOLSMITH = "toolsmith"
PROGRAM = "program"     # the chat routes for programs: a program key, or any owner credential
ACCESS_LEVELS = (PUBLIC, OWNER, OWNER_DEVICE, PACKAGE_OK, OWN_ROW, CORE, TOOLSMITH, PROGRAM)

_PREFIX = {"api_key": "ppk_", "device": "ppd_", "package_token": "ppt_", "toolsmith_token": "pts_"}
#: The label of every device token pairing issues to a browser (``groups.ui.ui_pair``, D79).
BROWSER_LABEL = "paired browser: "
#: A page pass on the home network (D130, ``page_pass``): how long one lasts. The Desktop gets new ones as it loads.
PAGE_PASS_S = 12 * 3600
PAGE_PASS_END_DIGITS = 12          # an end time is a number of seconds, not a number of any size
#: A paired browser's short id (``TokenStore.browsers``): enough to tell two apart, no use as a credential.
DEVICE_ID_CHARS = 16
#: The label of every program key ``puffpai apikey`` issues (``issue_program_key``): it opens only PROGRAM routes.
PROGRAM_LABEL = "program key: "


@dataclass(frozen=True)
class Caller:
    kind: str                      # "owner" | "package" | "toolsmith"
    via: str                       # "api_key" | "device" | "package_token" | "toolsmith_token"
    package_id: str | None = None
    label: str = ""
    build_id: str | None = None    # a Toolsmith token's one build
    bound: bool = False            # a browser's device token: counts only with its second half (D79)
    browser: bool = False          # issued by pairing a browser (BROWSER_LABEL): always needs its second half

    @property
    def needs_second_half(self) -> bool:
        """D79: a browser's token never counts alone, bound or not (an unbound one, from before D79, never
        counts at all). The terminal's and other programs' device keys are not browsers'."""
        return self.bound or self.browser

    @property
    def program(self) -> bool:
        """A program key (PROGRAM_LABEL): it reaches only PUBLIC and PROGRAM routes."""
        return self.via == "api_key" and self.label.startswith(PROGRAM_LABEL)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()    # any bytes a request can carry: only no match


def _page_signature(bind_hash: str, package_id: str, until: int) -> str:
    return hmac.new(bind_hash.encode("ascii"), f"page|{package_id}|{until}".encode("utf-8"), hashlib.sha256).hexdigest()


class TokenStore:
    """Issued credentials, hashed. The plain token is returned once, at issue."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS tokens ("
                " hash TEXT PRIMARY KEY, via TEXT NOT NULL, package_id TEXT,"
                " label TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            cols = {row[1] for row in db.execute("PRAGMA table_info(tokens)")}
            if "bind_hash" not in cols:           # D79: the second half of a browser's login
                db.execute("ALTER TABLE tokens ADD COLUMN bind_hash TEXT")
                # Once, on upgrade: every browser login from before D79 has no second half, and old browsers
                # still hold it as a 127.0.0.1 cookie that reaches every app's port. It ends now; she pairs again.
                db.execute("DELETE FROM tokens WHERE via = 'device' AND substr(label, 1, ?) = ?",
                           (len(BROWSER_LABEL), BROWSER_LABEL))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def _issue(self, via: str, label: str, package_id: str | None = None) -> str:
        token = _PREFIX[via] + secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            if via == "package_token":
                db.execute("DELETE FROM tokens WHERE via = 'package_token' AND package_id = ?", (package_id,))
            db.execute(
                "INSERT INTO tokens (hash, via, package_id, label, created_at) VALUES (?, ?, ?, ?, ?)",
                (_hash(token), via, package_id, label, datetime.now(timezone.utc).isoformat()),
            )
        return token

    def issue_owner_key(self, label: str = "owner key") -> str:
        return self._issue("api_key", label)

    def issue_program_key(self, label: str) -> str:
        """A program key (``puffpai apikey new``): it opens only the chat routes for programs."""
        return self._issue("api_key", PROGRAM_LABEL + label)

    def issue_device(self, label: str) -> str:
        return self._issue("device", label)

    def bind_device(self, token: str) -> str | None:
        """Bind a new second secret to a paired-device token (D79) and return it, once; only its SHA-256 is
        kept. None if *token* is not a paired device. A new bind replaces the old one."""
        secret = "ppb_" + secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            n = db.execute("UPDATE tokens SET bind_hash = ? WHERE hash = ? AND via = 'device'",
                           (_hash(secret), _hash(token))).rowcount
        return secret if n == 1 else None

    def bound_ok(self, token: str | None, secret: str | None) -> bool:
        """True only when *secret* is the second half bound to this device *token* (D79)."""
        if not token or not secret:
            return False
        with closing(self._connect()) as db:
            hit = db.execute("SELECT bind_hash FROM tokens WHERE hash = ? AND via = 'device'",
                             (_hash(token),)).fetchone()
        return bool(hit and hit[0]) and hmac.compare_digest(hit[0], _hash(secret))

    def page_pass(self, token: str | None, package_id: str, now: float | None = None) -> str | None:
        """D130: a pass for one package's page port on the home network, for a paired browser that showed both halves.
        ``<end time>.<signature>``, signed with this device's binding, which never leaves the Core. So it opens only that
        package, only on this bot, only until then, and stops when the device is removed. Nothing is saved. None if
        *token* has no binding."""
        with closing(self._connect()) as db:
            hit = db.execute("SELECT bind_hash FROM tokens WHERE hash = ? AND via = 'device'",
                             (_hash(token or ""),)).fetchone()
        if not hit or not hit[0]:
            return None
        until = int(time.time() if now is None else now) + PAGE_PASS_S
        return f"{until}.{_page_signature(hit[0], package_id, until)}"

    def page_pass_ok(self, value: str | None, package_id: str, now: float | None = None) -> bool:
        """True only when *value* is a live pass for this package from a browser still paired with this bot."""
        end, _, signature = str(value or "").partition(".")
        if not (end.isascii() and end.isdigit()) or len(end) > PAGE_PASS_END_DIGITS or not signature \
                or int(end) <= (time.time() if now is None else now):
            return False
        given = signature.encode("utf-8", "surrogatepass")
        with closing(self._connect()) as db:
            rows = db.execute("SELECT bind_hash FROM tokens WHERE via = 'device' AND bind_hash IS NOT NULL"
                              " AND substr(label, 1, ?) = ?", (len(BROWSER_LABEL), BROWSER_LABEL)).fetchall()
        return any(hmac.compare_digest(_page_signature(r[0], package_id, int(end)).encode("ascii"), given) for r in rows)

    def browsers(self) -> list[dict]:
        """Every browser pairing let in (D130): a short id, its label, when. Never a token or a usable hash."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT hash, label, created_at FROM tokens WHERE via = 'device' AND substr(label, 1, ?) = ?"
                              " ORDER BY created_at", (len(BROWSER_LABEL), BROWSER_LABEL)).fetchall()
        return [{"id": h[:DEVICE_ID_CHARS], "label": label[len(BROWSER_LABEL):], "created_at": at} for h, label, at in rows]

    def device_id(self, token: str | None) -> str:
        """The short id ``browsers`` shows for *token*, so a device can be told it is this one."""
        return _hash(token)[:DEVICE_ID_CHARS] if token else ""

    def revoke_browser(self, device_id: str) -> bool:
        """Remove one paired browser by its short id. It stops on its very next request, its page passes with it."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE via = 'device' AND substr(hash, 1, ?) = ?"
                              " AND substr(label, 1, ?) = ?",
                              (DEVICE_ID_CHARS, str(device_id), len(BROWSER_LABEL), BROWSER_LABEL)).rowcount > 0

    def issue_package_token(self, package_id: str) -> str:
        """A new ``PUFFPAI_TOKEN`` for one package; its old one stops working."""
        return self._issue("package_token", f"package {package_id}", package_id)

    def issue_toolsmith(self, build_id: str) -> str:
        """A Toolsmith token for one build (09 section 3); its old one stops working."""
        token = _PREFIX["toolsmith_token"] + secrets.token_urlsafe(32)
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM tokens WHERE via = 'toolsmith_token' AND package_id = ?", (build_id,))
            db.execute(
                "INSERT INTO tokens (hash, via, package_id, label, created_at) VALUES (?, ?, ?, ?, ?)",
                (_hash(token), "toolsmith_token", build_id, f"toolsmith {build_id}",
                 datetime.now(timezone.utc).isoformat()),
            )
        return token

    def revoke_toolsmith(self, build_id: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE via = 'toolsmith_token' AND package_id = ?",
                              (build_id,)).rowcount

    def lookup(self, token: str | None) -> Caller | None:
        if not token:
            return None
        with closing(self._connect()) as db:
            hit = db.execute("SELECT via, package_id, label, bind_hash FROM tokens WHERE hash = ?",
                             (_hash(token),)).fetchone()
        if not hit:
            return None
        via, package_id, label, bind_hash = hit
        if via == "toolsmith_token":
            return Caller(kind="toolsmith", via=via, label=label, build_id=package_id)
        return Caller(kind="package" if via == "package_token" else "owner", via=via,
                      package_id=package_id, label=label, bound=bool(bind_hash),
                      browser=via == "device" and label.startswith(BROWSER_LABEL))

    def revoke(self, token: str) -> bool:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE hash = ?", (_hash(token),)).rowcount > 0

    def revoke_device_label(self, prefix: str) -> int:
        """Revoke every paired-device token whose label starts with *prefix* (the terminal's, at Core start)."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE via = 'device' AND substr(label, 1, ?) = ?",
                              (len(prefix), prefix)).rowcount

    def revoke_unbound_browsers(self) -> int:
        """D79: revoke every browser login with no second half (paired before D79). Run at every Core start;
        the terminal's and other programs' device keys are left as they are."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE via = 'device' AND bind_hash IS NULL"
                              " AND substr(label, 1, ?) = ?", (len(BROWSER_LABEL), BROWSER_LABEL)).rowcount

    def program_keys(self) -> list[dict]:
        """Every program key as ``{label, created_at}`` (the label without PROGRAM_LABEL), oldest first. Never a
        hash, a token or any other key."""
        with closing(self._connect()) as db:
            rows = db.execute("SELECT label, created_at FROM tokens WHERE via = 'api_key'"
                              " AND substr(label, 1, ?) = ? ORDER BY created_at, label",
                              (len(PROGRAM_LABEL), PROGRAM_LABEL)).fetchall()
        return [{"label": label[len(PROGRAM_LABEL):], "created_at": created_at} for label, created_at in rows]

    def revoke_program_key(self, label: str) -> int:
        """Revoke every program key labelled exactly *label* (PROGRAM_LABEL + label); how many went."""
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE via = 'api_key' AND label = ?",
                              (PROGRAM_LABEL + label,)).rowcount

    def revoke_package(self, package_id: str) -> int:
        with closing(self._connect()) as db, db:
            return db.execute("DELETE FROM tokens WHERE package_id = ?", (package_id,)).rowcount


def credential(headers, cookies, allow_cookie: bool = True) -> tuple[str | None, bool]:
    """``(token, from_cookie)`` from a request's headers and, if allowed, its cookie."""
    auth = headers.get("Authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None, False
    key = headers.get("X-PuffPAI-Key", "").strip()
    if key:
        return key, False
    if not allow_cookie:
        return None, False
    cookie = cookies.get(COOKIE, "").strip()
    return (cookie or None), True


COOKIE = "puffpai_device"
#: The one port whose Core sets the plain name, so a sign-in from before D129 on a first bot keeps working.
COOKIE_USUAL_PORT = 8420
#: The second half of a browser's login (D79): sent by the Desktop from its localStorage, never a cookie.
DEVICE_HEADER = "X-PuffPAI-Device"
#: At most this many device cookies are checked in one request, over both names (each is one token lookup).
MAX_COOKIES = 16


def cookie_name(port: int) -> str:
    """The name a Core on *port* sets its device cookie under (D129): ``puffpai_device_<port>``, or the plain
    ``puffpai_device`` on the usual port. Cookies are not split by port, so one shared name lets each bot's
    sign-in replace the last one's."""
    return COOKIE if port == COOKIE_USUAL_PORT else f"{COOKIE}_{port}"


def cookie_tokens(cookies, where: int | str = COOKIE) -> list[str]:
    """The cookie values to look at, in the order sent, without repeats (D79). *where*: a Core's port, its own name first
    and then the plain name (D129), and another bot's name is never looked at; or one exact name, as the home network's
    listeners and page passes have (D130).

    The plain name is kept for a sign-in made before D129. Its values come second, so none planted there can use up
    the limit before the Core's own. A page on another ``*.localhost`` name can plant one under either name, with
    ``Domain=localhost`` and a longer path, so the browser sends it before the Desktop's own; the caller checks each
    against the token store."""
    out: list[str] = []
    for name in (where,) if isinstance(where, str) else dict.fromkeys((cookie_name(where), COOKIE)):
        values = cookies.getlist(name) if hasattr(cookies, "getlist") else [cookies.get(name, "")]
        for v in values:
            v = (v or "").strip()
            if v and v not in out:
                out.append(v)
            if len(out) >= MAX_COOKIES:
                return out
    return out


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
CSRF_HEADER = "X-PuffPAI"
CROSS_SITE = ("This request used the Desktop's login but did not come from the Desktop itself, "
              "so it was refused.")


def cookie_request_ok(method: str, headers, core_origins) -> bool:
    """A cookie-signed state change must come from the Desktop's own page (D49, D79).

    Its ``Origin`` must be one of the Core's own origins, ``http://puffpai.localhost:<port>`` (or, when a browser
    sends no Origin, ``Sec-Fetch-Site: same-origin``), and it must carry the
    custom ``X-PuffPAI: 1`` header, which no other page can add without a
    CORS preflight the Core never answers. SameSite cookies don't stop this on
    their own: they ignore ports, so any page on the same name is "same site".
    """
    if method.upper() in SAFE_METHODS:
        return True
    if headers.get(CSRF_HEADER, "").strip() != "1":
        return False
    origin = headers.get("Origin")
    if origin is not None:
        return origin.strip().rstrip("/").lower() in core_origins
    return headers.get("Sec-Fetch-Site", "").strip().lower() == "same-origin"


NO_CREDENTIAL = "Sign in first: send a paired device token or an owner API key (Authorization: Bearer <token>)."
#: The code on that refusal (D91, D92): a client counts a 401 as "signed out" only with this code (or none).
SIGN_IN_CODE = "sign_in"
PACKAGE_LIMIT = ("A package token can call only brain, notify, theme, people (read only), its own "
                 "Registry row, its own Checklist steps, its events and asking for one address for itself.")
TOOLSMITH_LIMIT = "A Toolsmith token can call only Toolsmith's own routes."
TOOLSMITH_ONLY = "This route is for Toolsmith's own process only."
PROGRAM_LIMIT = "A program key can call only the chat routes for programs (/openai/v1 and /ollama/api)."


def refusal(status: int, reason: str) -> dict:
    """The JSON body of a gate refusal: the reason, plus ``code: "sign_in"`` when no credential counted."""
    return {"error": reason, "code": SIGN_IN_CODE} if reason == NO_CREDENTIAL else {"error": reason}


def decide(access: str, caller: Caller | None, view_args: dict) -> tuple[int, str] | None:
    """None if the call may go ahead, else ``(status, plain reason)``."""
    if access == PUBLIC:
        return None
    if access == CORE:
        return 403, "This route is for the Core itself and is never served over HTTP."
    if caller is None:
        return 401, NO_CREDENTIAL
    if caller.kind == "toolsmith":
        return None if access == TOOLSMITH and caller.build_id else (403, TOOLSMITH_LIMIT)
    if access == TOOLSMITH:
        return 403, TOOLSMITH_ONLY
    if caller.kind == "owner":
        if caller.program:                # a program key: the chat routes for programs, nothing else
            return None if access == PROGRAM else (403, PROGRAM_LIMIT)
        if access == OWNER_DEVICE and caller.via != "device":
            return 403, "This needs the owner at a paired device; an API key can't do it."
        return None
    # A package token.
    if access == PACKAGE_OK:
        return None
    if access == OWN_ROW and view_args.get("id") == caller.package_id:
        return None
    return 403, PACKAGE_LIMIT
