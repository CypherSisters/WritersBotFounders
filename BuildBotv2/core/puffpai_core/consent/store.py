# Harvested in idea from PuffPAIBuildBot@63c8a037:src/puffpai/build/yes.py (sha and covers; rewritten)
"""Yes cards and the owner's answers: every Yes, minted only by the Core (X10, Z3 F2, D29).

One store for the whole Core (lane L4's store, with lane L1's names so the
Package Manager works unchanged). One SQLite file, ``<state>/consent.db``.

A **card** is what the owner sees. Two ways to raise one:

- ``request(scope, card, floor) -> consent_id`` (lane L1): ``scope`` is
  ``{kind, target}``, ``card`` is exactly what is shown. A ``package`` card's
  ``hosts`` and ``install_hosts`` become what its Yes covers on the Bridge.
- ``ask(kind, target, covers, floor=, bound=, expires=) -> card`` (lane L4):
  the Bridge's own cards (hosts, sends, device changes).

``card_sha`` is the hash of exactly what was shown AND everything the Yes
grants (scope and so the subject, floor, covers and so the hosts, bound,
expiry), so a Yes can't be reused for something else (D54). For lane L1's form
the store adds one key, ``puffpai_yes`` (scope, floor, covers), to the card
dict it is given, so L1's own ``card_sha(card)`` check covers them too. Each
card row is **sealed when it is created** with a MAC under the machine key, and
the seal, the fingerprint and every field the Yes would grant are checked
again before a card is shown or answered. A row edited outside PuffPAI (a
floor card turned into a tap card, another host in ``covers``) is ``void``:
it can't be answered, with a plain line. Grants are built only from the
verified, sealed row. A **record** is a card the owner answered Yes to.

Rules here:
- Nothing but :meth:`ConsentStore.answer` turns a card into a record, and only
  for a card the Core raised. No manifest, setting, provider, package or agent
  can mint one; the API route that answers is for a paired device only.
- **Floor-level cards need the PIN** (D29): a tap is refused; the PIN is checked
  by ``seams.check_pin`` (lane L3's Archivist for this bot folder, the one PIN check). No PIN
  check means no Yes, with a plain reason. Windows Hello is refused until it is
  built. A No is always allowed: it only narrows.
- Floor-level: ``send``, ``send_always``, ``device_change``, ``provider_swap``,
  ``relax_label``, and any card raised with ``floor=True``.
- ``tool_call`` (integration lane I1): one package tool call from chat that
  Drawbridge's review sends to her (outside her approved scope, or a tool it
  can't classify). Its target is ``<package>.<tool>``; the card shows the
  package, the tool and a gated summary of the arguments, and binds the Yes to
  those exact arguments (``args_sha``). Floor-level when Drawbridge says so (D29).
- ``model_pull`` (lane I17): one local model download, asked for by the Core (the Developer page, or a
  model offer she ticked on a plan card). Its target is ``model:<tag>``; the card names the model, its size
  and the host Ollama downloads from, and says the download goes around the Bridge (Ollama is its own
  program). Its Yes grants nothing on the Bridge.
- Pending cards are kept in the file (they survive a restart) and expire after
  10 minutes unanswered; listeners hear ``expired`` like a No.
- A record is live while it is ``granted``, not past ``expires``, its ``sha``
  recomputes and its MAC under the machine key checks (:mod:`..anchor`). The
  MAC also covers the store's **generation**: a counter kept in the file,
  MAC'd itself, that only goes up. Every revoke bumps it and re-seals the
  records still live, so a revoked row put back with its old status, sha and
  MAC (a row-level replay) fails: its MAC names an old generation. A copy of
  the whole old file passes here (its generation and rows agree); the
  generation is also kept in the vault (D54, :mod:`..zone3.vault_anchor`), so
  that is caught at the next unlock. A same-user edit of one row (a status
  flipped to granted, a forged row) is not a live record either.
- **A Yes must name the card it answers** (``card_sha``); a Yes without it is
  refused. A No needs no fingerprint: it only narrows.
- Events: ``consent.requested``, ``consent.granted`` (with ``bound`` when the
  card was bound) and ``consent.revoked``, published by the Core only, in the
  shapes of ``contracts/events.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .. import anchor as _anchor
from ..anchor import canon, sha

logger = logging.getLogger(__name__)

KINDS = ("package", "host", "key_use", "send", "send_always", "device_change", "cloud", "provider_swap",
         "owner_file", "relax_label", "tool_call", "model_pull")
FLOOR_KINDS = frozenset({"send", "send_always", "device_change", "provider_swap", "relax_label"})
FACTORS = ("tap", "pin", "hello")
STATUSES = ("pending", "granted", "declined", "expired", "revoked", "damaged", "void")
VOID_LINE = "This card was changed outside PuffPAI, so it can't be answered. Ask again to see it fresh."
CARD_TTL = timedelta(minutes=10)
#: Lane L3's PIN check by name (``seams.check_pin`` looks it up through the Archivist).
VERIFY_PIN = "zone1.archivist.verify_pin"

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,128}$")
_HASH_RE = re.compile(r"^[0-9a-f]{16,128}$")
_LABEL_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_PKG_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
_COLUMNS = (("id", "TEXT PRIMARY KEY"), ("scope", "TEXT NOT NULL"), ("floor", "INTEGER NOT NULL"),
            ("card", "TEXT NOT NULL"), ("card_sha", "TEXT NOT NULL"), ("status", "TEXT NOT NULL"),
            ("factor", "TEXT"), ("by", "TEXT"), ("created_at", "TEXT NOT NULL"), ("answered_at", "TEXT"),
            ("covers", "TEXT"), ("bound", "TEXT"), ("expires", "TEXT"), ("sha", "TEXT"), ("mac", "TEXT"),
            ("form", "TEXT"), ("seal", "TEXT"), ("note", "TEXT"))


class ConsentError(ValueError):
    """An answer that does not make a record: ``status`` (HTTP), ``code`` (closed), plain words."""

    def __init__(self, status: int, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


#: Lane L4's earlier name for the same error.
ConsentRefused = ConsentError


def card_sha(card: dict) -> str:
    """The hash of exactly what the owner was shown (lane L1's function, unchanged)."""
    return hashlib.sha256(json.dumps(card, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(t: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00"))
    except ValueError:
        return None


def _bound_ok(bound: Any) -> bool:
    """One of the three ``consent.granted.bound`` shapes in contracts/events.json, exactly."""
    if bound is None:
        return True
    if not isinstance(bound, dict):
        return False
    labels = bound.get("labels")
    labels_ok = isinstance(labels, list) and all(isinstance(x, str) and _LABEL_RE.match(x) for x in labels)
    keys = set(bound)
    if keys == {"payload_sha", "labels"}:
        return isinstance(bound["payload_sha"], str) and bool(_HASH_RE.match(bound["payload_sha"])) and labels_ok
    if keys == {"package", "labels"}:
        return isinstance(bound["package"], str) and bool(_PKG_RE.match(bound["package"])) and labels_ok
    if keys == {"plan_sha"}:
        return isinstance(bound["plan_sha"], str) and bool(_HASH_RE.match(bound["plan_sha"]))
    return False


def _l1_covers(kind: Any, card: dict) -> dict:
    """What a lane L1 card's Yes covers on the Bridge: a package card's own ``hosts`` and ``install_hosts``."""
    covers: dict = {}
    if kind == "package":
        for key in ("hosts", "install_hosts"):
            items = card.get(key) or []
            if isinstance(items, list) and all(isinstance(x, str) for x in items):
                covers[key] = list(items)
    return covers


class ConsentStore:
    """Cards and records in ``<state>/consent.db``.

    ``verify_pin(pin) -> bool`` overrides ``seams.check_pin`` (tests and the
    Core's wiring); ``anchor`` is the machine-key MAC (default: the bot folder's).
    """

    def __init__(self, path: Path | str, bus: Any = None, *, anchor: _anchor.Anchor | None = None,
                 verify_pin: Callable[[str], bool] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.bus = bus
        self.anchor = anchor or _anchor.for_state(self.path.parent)
        self.verify_pin = verify_pin
        self._cond = threading.Condition(threading.RLock())
        self.write_lock = threading.RLock()    # D151: held across a new generation and its seal; the unlock check reads under it
        self._answer_listeners: list[Callable[[dict], None]] = []
        self._yes_listeners: list[Callable[[dict], None]] = []
        self.on_generation: Callable[[int], None] | None = None   # D54: the vault copy (zone3.vault_anchor)
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL, mac TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS cards (" + ", ".join(f"{c} {t}" for c, t in _COLUMNS) + ")")
            have = {r[1] for r in db.execute("PRAGMA table_info(cards)")}
            for c, t in _COLUMNS:
                if c not in have:              # a file from lane L1's first schema
                    db.execute(f"ALTER TABLE cards ADD COLUMN {c} {t.replace(' NOT NULL', '')}")
            has_gen = db.execute("SELECT 1 FROM meta WHERE name = 'generation'").fetchone()
            empty = db.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0
            if not has_gen and empty and self.anchor.available:
                # A new store starts at generation 1. A missing generation in a store that has cards reads
                # as damaged (never as 1), so deleting it can't bring old rows back.
                db.execute("INSERT INTO meta (name, value, mac) VALUES ('generation', '1', ?)", (self._gen_mac(1),))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    # -- the generation ------------------------------------------------------------
    def _gen_mac(self, g: int) -> str:
        return self.anchor.mac("consent_generation", f"{self.path.name}:{g}")

    def generation(self, db: sqlite3.Connection | None = None) -> int | None:
        """The store's generation (1 for a new file), or None when it was edited or can't be checked."""
        conn = db or self._connect()
        try:
            row = conn.execute("SELECT value, mac FROM meta WHERE name = 'generation'").fetchone()
        finally:
            if db is None:
                conn.close()
        if row is None:
            return None
        try:
            g = int(row[0])
        except (TypeError, ValueError):
            return None
        return g if g >= 1 and self.anchor.check("consent_generation", f"{self.path.name}:{g}", row[1]) else None

    def _rec_mac(self, rsha: str, g: int) -> str:
        return self.anchor.mac("consent", f"{g}:{rsha}")

    # -- listeners ------------------------------------------------------------
    def on_answer(self, fn: Callable[[dict], None]) -> None:
        """Call *fn(record)* after every answer: yes, no, or expired (lane L1)."""
        self._answer_listeners.append(fn)

    def on_granted(self, fn: Callable[[dict], None]) -> None:
        """Call *fn(record)* after each Yes only (the Bridge turns package and host Yeses into grants)."""
        self._yes_listeners.append(fn)

    def _tell(self, fns: Iterable[Callable[[dict], None]], record: dict) -> None:
        for fn in list(fns):
            try:
                fn(dict(record))
            except Exception:  # noqa: BLE001 - one listener's bug never undoes her answer or stops the others
                logger.exception("consent listener failed for %s", record.get("consent_id"))

    # -- raising cards ----------------------------------------------------------
    def request(self, scope: dict, card: dict, floor: bool) -> str:
        """Lane L1's form: raise a card showing exactly *card*. Returns its ``consent_id``."""
        if not isinstance(scope, dict) or not isinstance(card, dict):
            raise ValueError("a card needs a scope and what it shows")
        if "puffpai_yes" in card:
            raise ValueError("a card's puffpai_yes key is the store's own")
        covers = _l1_covers(scope.get("kind"), card)
        return self._raise(scope.get("kind"), scope.get("target"), covers, floor=floor, bound=None,
                           expires=None, shown=card)["consent_id"]

    def ask(self, kind: str, target: str, covers: dict | None = None, *, floor: bool = False,
            bound: dict | None = None, expires: str | None = None) -> dict:
        """Lane L4's form: raise one card. Returns it (``consent_id``, ``card_sha``, ``floor``, ...)."""
        return self._raise(kind, target, covers or {}, floor=floor, bound=bound, expires=expires, shown=None)

    def _raise(self, kind: Any, target: Any, covers: dict, *, floor: bool, bound: dict | None,
               expires: str | None, shown: dict | None) -> dict:
        if kind not in KINDS:
            raise ValueError(f"unknown consent kind {kind!r}")
        if not isinstance(target, str) or not _ID_RE.match(target):
            raise ValueError("a consent target is a short id")
        if not _bound_ok(bound):
            raise ValueError("bound is one of {payload_sha, labels}, {package, labels} or {plan_sha}")
        if expires is not None and _parse(expires) is None:
            raise ValueError("expires is a time")
        if not self.anchor.available:
            raise ConsentError(503, _anchor.NO_KEY_LINE, "no_machine_key")    # an unsealed card is never raised
        is_floor = bool(floor) or kind in FLOOR_KINDS
        scope = {"kind": kind, "target": target}
        yes = {"scope": scope, "covers": covers, "floor": is_floor, "bound": bound, "expires": expires}
        if shown is not None:
            form = "l1"
            shown["puffpai_yes"] = {"scope": scope, "floor": is_floor, "covers": covers}   # in L1's own dict
            card = shown
        else:
            form, card = "l4", yes
        csha = card_sha(json.loads(json.dumps(card, ensure_ascii=False)))   # as it will be read back
        cid = "c_" + secrets.token_hex(12)
        created = _iso(_now())
        row = {"id": cid, "form": form, "scope": scope, "floor": is_floor, "card_sha": csha,
               "covers": json.loads(canon(covers)), "bound": json.loads(canon(bound)) if bound is not None else None,
               "expires": expires, "created_at": created}
        seal = self.anchor.mac("consent_card", canon(row))
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO cards (id, scope, floor, card, card_sha, status, created_at, covers, bound,"
                       " expires, form, seal) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
                       (cid, canon(scope), int(is_floor), json.dumps(card, ensure_ascii=False), csha, created,
                        canon(covers), canon(bound) if bound is not None else None, expires, form, seal))
        self._publish("consent.requested", {"consent_id": cid, "scope": scope, "floor": is_floor, "card_sha": csha})
        out = self.get(cid) or {}
        return out

    # -- reading ----------------------------------------------------------------
    def _row(self, cid: str) -> tuple | None:
        with closing(self._connect()) as db:
            return db.execute("SELECT " + ", ".join(c for c, _ in _COLUMNS) + " FROM cards WHERE id = ?",
                              (cid,)).fetchone()

    @staticmethod
    def _record_sha(rec: dict) -> str:
        return sha({k: rec.get(k) for k in ("consent_id", "scope", "covers", "floor", "bound", "expires",
                                            "card_sha", "factor", "by", "answered_at")})

    def _record(self, row: tuple) -> dict:
        d = dict(zip((c for c, _ in _COLUMNS), row))
        try:
            rec = {"consent_id": d["id"], "scope": json.loads(d["scope"]), "floor": bool(d["floor"]),
                   "card": json.loads(d["card"]), "card_sha": d["card_sha"], "status": d["status"],
                   "factor": d["factor"], "by": d["by"], "created_at": d["created_at"],
                   "answered_at": d["answered_at"], "covers": json.loads(d["covers"]) if d["covers"] else {},
                   "bound": json.loads(d["bound"]) if d["bound"] else None, "expires": d["expires"]}
        except (TypeError, ValueError):
            return {"consent_id": d.get("id"), "scope": {}, "floor": True, "card": {}, "card_sha": "",
                    "status": "damaged", "factor": None, "by": None, "created_at": d.get("created_at"),
                    "answered_at": None, "covers": {}, "bound": None, "expires": None}
        rec["at"] = rec["answered_at"]
        if d.get("note"):
            rec["note"] = d["note"]
        if not self._sealed(d, rec):
            rec["status"] = "void"             # edited outside PuffPAI: never shown as answerable, never a Yes
            rec["line"] = VOID_LINE
            return rec
        if rec["status"] == "granted":
            g = self.generation()
            good = g is not None and d["sha"] == self._record_sha(rec) \
                and self.anchor.check("consent", f"{g}:{d['sha'] or ''}", d["mac"])
            if good:
                rec["sha"] = d["sha"]
            else:
                rec["status"] = "damaged"      # edited outside PuffPAI, or no machine key: not a Yes
        elif rec["status"] not in STATUSES:
            rec["status"] = "damaged"
        return rec

    def _sealed(self, d: dict, rec: dict) -> bool:
        """The row's seal, its fingerprint and every field the Yes grants all agree."""
        try:
            card, scope, form = rec["card"], rec["scope"], d.get("form")
            if form not in ("l1", "l4") or not isinstance(card, dict) or not isinstance(scope, dict):
                return False
            if card_sha(card) != rec["card_sha"]:
                return False                   # what is shown is not what was fingerprinted
            if scope.get("kind") in FLOOR_KINDS and not rec["floor"]:
                return False
            if form == "l4":
                yes = card
                want = {"scope": scope, "covers": rec["covers"], "floor": rec["floor"], "bound": rec["bound"],
                        "expires": rec["expires"]}
                if yes != want:
                    return False
            else:
                yes = card.get("puffpai_yes")
                if yes != {"scope": scope, "floor": rec["floor"], "covers": rec["covers"]}:
                    return False
                if rec["covers"] != _l1_covers(scope.get("kind"), card) or rec["bound"] or rec["expires"]:
                    return False               # an L1 card grants only the hosts it shows
            row = {"id": d["id"], "form": form, "scope": scope, "floor": rec["floor"], "card_sha": rec["card_sha"],
                   "covers": rec["covers"], "bound": rec["bound"], "expires": rec["expires"],
                   "created_at": d["created_at"]}
            return self.anchor.check("consent_card", canon(row), d.get("seal"))
        except (KeyError, TypeError, AttributeError):
            return False

    def get(self, cid: str | None) -> dict | None:
        """Any card or record by id (lane L1), with its ``status``; None if unknown."""
        if not isinstance(cid, str) or not cid:
            return None
        self._sweep()
        row = self._row(cid)
        return self._record(row) if row else None

    def list(self, status: str | None = None) -> list[dict]:
        """Every card, oldest first; ``status`` filters (pending, granted, declined, expired, revoked)."""
        self._sweep()
        with closing(self._connect()) as db:
            rows = db.execute("SELECT " + ", ".join(c for c, _ in _COLUMNS)
                              + " FROM cards ORDER BY created_at, id").fetchall()
        out = [self._record(r) for r in rows]
        return [r for r in out if status is None or r["status"] == status]

    def card(self, consent_id: str) -> dict | None:
        """A card still waiting for an answer, or None."""
        rec = self.get(consent_id)
        return rec if rec and rec["status"] == "pending" else None

    def granted(self, consent_id: str | None) -> dict | None:
        """A live Yes: granted, its sha and MAC check, not past ``expires``. Else None."""
        rec = self.get(consent_id)
        if not rec or rec["status"] != "granted":
            return None
        if rec.get("expires"):
            exp = _parse(rec["expires"])
            if exp is None or exp < _now():
                return None
        return rec

    def live(self, kind: str | None = None) -> list[dict]:
        out = [r for r in (self.granted(c["consent_id"]) for c in self.list("granted")) if r]
        return [r for r in out if kind is None or r["scope"]["kind"] == kind]

    # -- expiry -----------------------------------------------------------------
    def _sweep(self) -> None:
        """Pending cards older than CARD_TTL become ``expired``; listeners hear it like a No."""
        cutoff = _iso(_now() - CARD_TTL)
        with closing(self._connect()) as db:
            ids = [r[0] for r in db.execute("SELECT id FROM cards WHERE status = 'pending' AND created_at < ?",
                                            (cutoff,)).fetchall()]
        for cid in ids:
            with self._cond:
                with closing(self._connect()) as db, db:
                    changed = db.execute("UPDATE cards SET status = 'expired', answered_at = ? WHERE id = ? "
                                         "AND status = 'pending'", (_iso(_now()), cid)).rowcount
                self._cond.notify_all()
            if changed:
                row = self._row(cid)
                if row:
                    self._tell(self._answer_listeners, self._record(row))

    # -- answering --------------------------------------------------------------
    def _check_pin(self, pin: Any) -> None:
        text = pin if isinstance(pin, str) else ""
        if self.verify_pin is not None:
            try:
                result: bool | None = self.verify_pin(text) is True
            except Exception:  # noqa: BLE001 - a crashing check is a no
                result = False
        else:
            # The one PIN check (``seams.check_pin``, lane L3's Archivist for this bot folder).
            from .. import seams
            refused = seams.check_pin(self.path.parent, text, self.bus)
            if refused is not None:
                raise ConsentError(*refused)
            result = True
        if result is None:
            raise ConsentError(503, "The PIN check is not here yet, so this card can't be answered Yes.",
                               "no_pin_check")
        good = result is True
        if not good:
            raise ConsentError(403, "That PIN is not right.", "step_up_failed")

    def answer(self, consent_id: str, answer: str, factor: str = "tap", pin: str | None = None, by: str = "",
               *, card_sha: str | None = None) -> dict:
        """The owner's answer to a card. Returns the record. Raises :class:`ConsentError` when refused."""
        if answer not in ("yes", "no"):
            raise ConsentError(400, 'The answer is "yes" or "no".', "bad_answer")
        if factor not in FACTORS:
            raise ConsentError(400, 'The factor is "tap", "pin" or "hello".', "bad_factor")
        self._sweep()
        rec = self.get(consent_id)
        if rec is None:
            raise ConsentError(404, "That card is not waiting for an answer.", "no_card")
        if rec["status"] == "void":
            raise ConsentError(409, VOID_LINE, "void")
        if rec["status"] == "expired":
            raise ConsentError(409, rec.get("note") or "That card expired. Ask again to see it fresh.", "expired")
        if rec["status"] != "pending":
            raise ConsentError(409, f"This card was already answered ({rec['status']}).", "answered")
        if answer == "yes" and not card_sha:
            raise ConsentError(400, "A Yes must name the card it answers (its card_sha), so nothing else can be "
                               "approved by mistake.", "no_card_sha")
        if card_sha is not None and card_sha != rec["card_sha"]:
            raise ConsentError(409, "That card changed after you saw it. Look at it again.", "card_changed")
        if answer == "yes":
            if factor == "hello":
                raise ConsentError(403, "Windows Hello is not built yet. Use your PIN for this card.",
                                   "hello_not_built")
            if rec["floor"]:
                if factor == "tap":
                    raise ConsentError(403, "This card needs your PIN; a tap is not enough for it.", "needs_pin")
                self._check_pin(pin)
            if not self.anchor.available:
                raise ConsentError(503, _anchor.NO_KEY_LINE, "no_machine_key")
        status = "granted" if answer == "yes" else "declined"
        at = _iso(_now())
        who = str(by or "owner")[:64]
        new = dict(rec, factor=factor, by=who, answered_at=at)
        rsha = self._record_sha(new) if status == "granted" else None
        with self._cond:
            with closing(self._connect()) as db, db:
                g = self.generation(db)
                if rsha and g is None:
                    raise ConsentError(503, "PuffPAI's record of your Yeses was changed outside PuffPAI, so no "
                                       "new Yes can be added until it is checked.", "store_damaged")
                mac = self._rec_mac(rsha, g) if rsha else None
                changed = db.execute("UPDATE cards SET status = ?, factor = ?, by = ?, answered_at = ?, sha = ?, "
                                     "mac = ? WHERE id = ? AND status = 'pending'",
                                     (status, factor, who, at, rsha, mac, consent_id)).rowcount
            self._cond.notify_all()
        if not changed:
            raise ConsentError(409, "This card was already answered.", "answered")
        out = self.get(consent_id) or {}
        out["answer"] = answer
        if status == "granted":
            payload: dict[str, Any] = {"consent_id": consent_id, "scope": out["scope"], "factor": factor,
                                       "expires": out.get("expires")}
            if out.get("bound"):
                payload["bound"] = out["bound"]
            self._publish("consent.granted", payload)
            self._tell(self._yes_listeners, out)
        self._tell(self._answer_listeners, out)
        return out

    def expire(self, consent_id: str, note: str = "") -> bool:
        """End a waiting card with no Yes (Core only; lane L1 calls it at start for cards left from the last
        run). True when it was waiting. Listeners hear ``expired`` like a No."""
        with self._cond:
            with closing(self._connect()) as db, db:
                changed = db.execute("UPDATE cards SET status = 'expired', by = 'core', answered_at = ?, note = ? "
                                     "WHERE id = ? AND status = 'pending'",
                                     (_iso(_now()), str(note or "")[:200], consent_id)).rowcount
            self._cond.notify_all()
        if changed:
            rec = self.get(consent_id)
            if rec:
                self._tell(self._answer_listeners, rec)
        return bool(changed)

    def wait(self, consent_id: str, timeout_s: float | None = None) -> dict | None:
        """Block until the card is answered or expires (or *timeout_s* passes). The record, or None."""
        deadline = None if timeout_s is None else _now() + timedelta(seconds=timeout_s)
        with self._cond:                       # answer() updates and notifies under this lock
            while True:
                rec = self.get(consent_id)
                if rec is None or rec["status"] != "pending":
                    return rec
                left = 5.0 if deadline is None else (deadline - _now()).total_seconds()
                if left <= 0:
                    return rec
                self._cond.wait(timeout=min(left, 5.0))

    def revoke(self, consent_id: str) -> bool:
        """Take a Yes back. Bumps the generation and re-seals every record still live, in one
        transaction, so the revoked row (or any older copy of a row) can't be put back."""
        rec = self.granted(consent_id)
        if rec is None:
            return False
        live = [r for r in self.live() if r["consent_id"] != consent_id]
        with self.write_lock:
            with self._cond:
                with closing(self._connect()) as db, db:
                    g = self.generation(db)
                    if g is None:
                        return False
                    changed = db.execute("UPDATE cards SET status = 'revoked', mac = NULL WHERE id = ? AND "
                                         "status = 'granted'", (consent_id,)).rowcount
                    if not changed:
                        return False
                    new = g + 1
                    db.execute("INSERT OR REPLACE INTO meta (name, value, mac) VALUES ('generation', ?, ?)",
                               (str(new), self._gen_mac(new)))
                    for r in live:
                        db.execute("UPDATE cards SET mac = ? WHERE id = ? AND status = 'granted'",
                                   (self._rec_mac(r["sha"], new), r["consent_id"]))
            if self.on_generation is not None:
                try:
                    self.on_generation(new)
                except Exception:  # noqa: BLE001 - the revoke stands; the vault copy is retried at unlock
                    logger.warning("could not hand the consent generation to the vault")
        self._publish("consent.revoked", {"consent_id": consent_id, "scope": rec["scope"]})
        return True

    # -- what the Bridge asks -------------------------------------------------------
    def find_send(self, payload_sha: str | None, labels: list[str], package: str | None) -> dict | None:
        """A live Yes for this exact payload and these labels (``ask_each``), or the owner's
        "always for this app and this kind of data" (``send_always``) for *package*."""
        want = set(labels)
        for rec in self.live():
            kind, bound = rec["scope"]["kind"], rec.get("bound") or {}
            if not want <= set(bound.get("labels") or []):
                continue
            if kind == "send" and payload_sha and bound.get("payload_sha") == payload_sha:
                return rec
            if kind == "send_always" and package and bound.get("package") == package:
                return rec
        return None

    def find_always(self, package: str | None, labels: list[str]) -> dict | None:
        want = set(labels)
        for rec in self.live("send_always"):
            bound = rec.get("bound") or {}
            if package and bound.get("package") == package and want <= set(bound.get("labels") or []):
                return rec
        return None

    def _publish(self, type_: str, payload: dict) -> None:
        if self.bus is None:
            return
        from ..bus import Bus, Source
        self.bus.publish(Bus.message(type_, payload, Source("core", "core:consent")))


__all__ = ["CARD_TTL", "ConsentError", "ConsentRefused", "ConsentStore", "FLOOR_KINDS", "KINDS", "STATUSES",
           "VERIFY_PIN", "VOID_LINE", "canon", "card_sha", "sha"]
