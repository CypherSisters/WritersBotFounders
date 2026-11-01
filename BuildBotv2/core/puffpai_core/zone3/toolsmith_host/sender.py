# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/toolsmith_cloud.py (adapted)
"""The Core's sender for a frontier model for Toolsmith (slot ``zone3.toolsmith-model``, D35, D42-D44, D52).

This runs in the Core process; Toolsmith's own process never imports it. It
reaches it only through its ``model`` route ("lend and send", 09 section 4):
Toolsmith sends the neutral request (its prompt), the Core does the rest:
- **The screen**: key shapes out (``zone1.secret_detector``), personal
  details out (the outbound screen, ``ports.screen_outbound``), her account
  name becomes "you". If a screen can't run, nothing is sent.
- **The mapping** (``wire``, for a data-only provider) only translates. It
  never sees the key: it writes ``{key}`` in the declared header.
- **The final wire body** gets the key screen AND the outbound screen again,
  on every field name and every value (09 section 4: an adapter can't add
  back what it was never given); any hit stops the send.
- **The company's own returned items** (``opaque`` blocks: signed thinking,
  reasoning items, thought signatures) are the only thing not screened, and
  only when the Core itself received them from that same provider in this
  build: each one gets an id, and the Core keeps an HMAC of its exact bytes.
  An opaque block Toolsmith's process made up or changed stops the send.
- **The host** is the provider's own and on the Core's known list.
- **The key** is borrowed from Archivist as a lease handle and put in at the
  moment of sending; its bytes never leave this process (D41).
- **Prices** (D52): for a company on the known list the Core's own price
  table is used (the built-in providers shipped in ``core\\``); an adapter's
  prices only count when they are higher. A package can't use a built-in
  provider's id.
- **The cap** (D43, default $5 per build): before each call the Core reserves
  the worst case (every body byte a token at the dearest input price, plus
  the request's full maximum reply length at the output price, D52), and
  holds the build with :data:`CAP_LINE` if that could cross the cap. A reply
  with no usage count counts as the cap (X29).
- **Offline**: an unreachable host raises ``CloudOffline``; the try goes on
  with this computer's model and says :data:`OFFLINE_LINE`.

v1 sources in ``security/toolsmith_cloud.py``: ``CONSENT_TEXT`` :119,
``KEY_LINE`` and ``OFFLINE_LINE`` :128-129, ``clean`` :1019, ``_retry_wait``
:1103, ``cost_usd`` :1230, ``record_usage`` :1254 (adapted: many providers,
a cap, the key lent as a handle, the Core's price table).
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib.resources
import json
import random
import re
import secrets
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from ...zone1 import secret_detector
from ..toolsmith.errors import CloudError, CloudHeld, CloudOffline, CloudWaitVault, NotBuilt, Refused
from ..toolsmith.names import scrub_name
from . import wire
from .ports import KeyUnavailable, Redirected

#: The built-in providers and the known list ship as package data (they work from a wheel too).
BUILTIN_DIR = importlib.resources.files(__package__).joinpath("providers")
DATA_DIR = importlib.resources.files(__package__).joinpath("data")

#: The Core's what-is-sent list: every provider's consent sentence must hold
#: each of these, and name its own company (``puffpai check`` compares them).
SENT_ITEMS = ("your goal", "your bot's jobs", "the names and descriptions of the apps it found",
              "README", "other text files from the app's own download",
              "the commands it runs with their output", "what the app's pages show",
              "so it can read your files", "Keys and personal details it spots are taken out first",
              "but it can miss some")

CAP_LINE = ("Toolsmith reached the ${cap} limit you set for this build. Raise it, or let Toolsmith "
            "use this computer.")
OFFLINE_LINE = "Toolsmith could not reach {company}, so it uses the model on this computer for this app."
KEY_LINE = "Toolsmith's {company} key was not accepted. Check it in Settings."
NO_KEY_LINE = ("You picked {company} for Toolsmith, but its key isn't saved yet. Open Settings to add "
               "it, or let Toolsmith use this computer.")
#: Her saved Yes is for an earlier sentence, so nothing is sent until she says Yes again (D101, D118).
CHANGED_LINE = "What Toolsmith sends to {company} now reads differently."
RECONSENT_LINE = ("{change} Read it again in Settings and say yes, or let Toolsmith use this computer. "
                  "Then Check again to retry.")
#: What is new since an earlier sentence, by that sentence's sha: the built-in providers' sentences before D118.
_D118_NEW = ("It now also sends other text files from each app's own download, like its source code. It also says "
             "that a try can read your files, and that what it shows goes to {company}.")
EARLIER_SENTENCES = {"d196a618e87e569b96daa989bd0c5ec4b2bef367e9071a4960d47c1ba2e5a910": _D118_NEW,    # Anthropic
                     "3fa3af9b9d11ec25a8e19b39ba93f83d1df827a73da52986feaef3c79b5b9b96": _D118_NEW,    # Google
                     "359a1350c927807804eba38ab2bc67ee33fafa00988fe623bd629e12ece68785": _D118_NEW}    # OpenAI
VAULT_LINE = "Unlock your vault to let Toolsmith go on."
#: Her Yes lends Toolsmith its key for the build, 3 hours at most (X22, D41, D68): past that it waits, said plainly.
LEASE_CAP_LINE = ("Your Yes lends Toolsmith its key for 3 hours at most, and this build has run that long. "
                  "Unlock your vault to let it go on.")
REDIRECT_LINE = "{company} answered with a redirect, so nothing more was sent. Toolsmith stopped this try."
PRIVACY_LINE = "The privacy check could not run, so nothing was sent to {company}."
OPAQUE_LINE = ("Toolsmith sent back something {company} never sent it, so nothing was sent. "
               "Toolsmith stopped this try.")
SHADOW_LINE = "Its id belongs to a provider PuffPAI ships, so a package can't use it."

RETRIES = 4
MAX_WAIT_S = 60.0
MAX_BODY_BYTES = 3_000_000
MAX_TOKENS = 16000
FIND_MAX_TOKENS = 2048
CALL_TIMEOUT = 300.0
PRICE_FIELDS = ("in", "out", "cache_read", "cache_write")


# -- providers --------------------------------------------------------------------

def _json(node: Any) -> Any:
    return json.loads(node.read_text(encoding="utf-8"))


def known_hosts() -> dict[str, list[str]]:
    data = _json(DATA_DIR.joinpath("known_hosts.json"))
    return {str(k): [str(h) for h in v] for k, v in data["companies"].items()}


def _is_provider(m: Any) -> bool:
    if not isinstance(m, dict):
        return False
    slots = [p.get("slot") for p in m.get("provides") or [] if isinstance(p, dict)]
    return m.get("kind") == "provider" and "zone3.toolsmith-model" in slots and bool(m.get("toolsmith_model"))


def builtin_providers() -> dict[str, dict]:
    """The providers the Core ships (Anthropic, OpenAI, Google, D44), from package data."""
    out: dict[str, dict] = {}
    for folder in sorted(BUILTIN_DIR.iterdir(), key=lambda t: t.name):
        man = folder.joinpath("puffpai.json")
        if not folder.is_dir() or not man.is_file():
            continue
        m = _json(man)
        if _is_provider(m):
            out[str(m["id"])] = m
    return out


def builtin_ids() -> frozenset[str]:
    return frozenset(builtin_providers())


def load_providers(*dirs: Path | str) -> dict[str, dict]:
    """``{id: manifest}``: the built-in providers, then every ``zone3.toolsmith-model`` provider in
    *dirs* (one ``puffpai.json`` per folder; tests and tools only: the running Core uses
    :func:`installed_providers`). A package can never take a built-in id (D52)."""
    found = []
    for root in dirs:
        root = Path(root)
        if not root.is_dir():
            continue
        for man in sorted(root.glob("*/puffpai.json")):
            try:
                found.append(json.loads(man.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return _with_builtins(found)


def installed_providers(registry: Any, read: Any) -> dict[str, dict]:
    """``{id: manifest}``: the built-in providers, then each INSTALLED provider package (a Registry row of
    kind provider) whose manifest the Core's one reader gives (*read(id)*: the file her install Yes
    covered, by its sha256). A folder in ``packages`` with no row (a try's download) is never read."""
    found = []
    for row in (registry.list(kind="provider") if registry is not None else []):
        if row.get("status") in ("removed", "broken"):
            continue
        try:
            m = read(row["id"])
        except Exception:  # noqa: BLE001 - a reader that breaks: not loaded
            m = None
        if isinstance(m, dict) and str(m.get("id")) == row["id"]:
            found.append(m)
    return _with_builtins(found)


#: Ids no provider package may take, besides the built-ins' own: names the Core keeps for its own vault keys.
#: ``toolsmith.github`` is the owner's GitHub search key (lane I15), so a package with id ``github`` would borrow it
#: for its own hosts; the same for her own web search keys. Compared without case, as the built-ins are.
RESERVED_IDS = frozenset({"github", "brave", "marginalia"})


def _with_builtins(manifests: list) -> dict[str, dict]:
    out = builtin_providers()
    taken = {str(k).lower() for k in out} | RESERVED_IDS
    for m in manifests:
        if not _is_provider(m) or str(m.get("id")).strip().lower() in taken:
            continue                                # a shadow of a built-in, a reserved id or a repeat: not loaded
        taken.add(str(m["id"]).strip().lower())
        out[str(m["id"])] = m
    return out


def core_prices() -> dict[str, dict[str, dict]]:
    """The Core's price table (D52): ``{company: {model id: prices}}`` from the built-in providers."""
    table: dict[str, dict[str, dict]] = {}
    for m in builtin_providers().values():
        tm = m["toolsmith_model"]
        for model in tm.get("models") or []:
            table.setdefault(str(tm["company"]), {})[str(model["id"])] = _full_prices(model.get("prices"))
    return table


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if f >= 0 and f == f and f != float("inf") else 0.0


def _full_prices(p: Any) -> dict:
    p = p if isinstance(p, dict) else {}
    pin = _num(p.get("in"))
    return {"in": pin, "out": _num(p.get("out")),
            "cache_read": _num(p["cache_read"]) if "cache_read" in p else pin,
            "cache_write": _num(p["cache_write"]) if "cache_write" in p else pin * 1.25}


def effective_prices(provider: dict, model_id: str) -> dict | None:
    """The prices the cap counts with (D52). None: no such model."""
    tm = provider.get("toolsmith_model") or {}
    declared = next((m.get("prices") for m in tm.get("models") or [] if m.get("id") == model_id), None)
    if declared is None:
        return None
    mine = _full_prices(declared)
    table = core_prices().get(str(tm.get("company") or ""))
    if table is None:
        return mine                                  # not a known company: it needs Verified anyway
    base = table.get(model_id)
    if base is None:                                 # a model the Core has no price for: its dearest one
        base = {f: max((row[f] for row in table.values()), default=0.0) for f in PRICE_FIELDS}
    return {f: max(base[f], mine[f]) for f in PRICE_FIELDS}


def consent_sha(provider: dict) -> str:
    return hashlib.sha256(provider["toolsmith_model"]["consent"].encode("utf-8")).hexdigest()


def consent_change(provider: dict, given: Any) -> str:
    """Why her saved Yes for *provider* no longer covers what is sent, in plain words, with what is new when the
    Core knows it. "" with no saved Yes, or a Yes for this very sentence: a first-time owner never sees this."""
    if not given or given == consent_sha(provider):
        return ""
    line = CHANGED_LINE.format(company=provider["toolsmith_model"]["company"])
    new = EARLIER_SENTENCES.get(str(given))
    return f"{line} {new.format(company=provider['toolsmith_model']['company'])}" if new else line


def provider_problems(provider: dict) -> list[str]:
    """Why this provider may not be used as a data-only adapter ([] = fine)."""
    tm = provider.get("toolsmith_model") or {}
    company, hosts = str(tm.get("company") or ""), list(tm.get("hosts") or [])
    out: list[str] = []
    builtin = builtin_providers().get(str(provider.get("id") or ""))
    if builtin is not None and builtin != provider:
        out.append(SHADOW_LINE)
    listed = known_hosts().get(company)
    if listed is None:
        out.append(f"{company or 'This company'} is not on the Core's known list, so it needs Verified.")
    else:
        out += [f"{h} is not a listed host for {company}." for h in hosts if h not in listed]
    if tm.get("wire") not in wire.WIRES:
        out.append("It names no mapping the Core runs, so it needs a Verified adapter.")
    consent = str(tm.get("consent") or "")
    if company and company not in consent:
        out.append(f"Its consent sentence does not name {company}.")
    out += [f"Its consent sentence leaves out: {item}." for item in SENT_ITEMS if item not in consent]
    ids = [str(m.get("id")) for m in tm.get("models") or []]
    if tm.get("default_model") is not None and tm.get("default_model") not in ids:
        out.append("Its default model is not in its model list.")
    try:
        re.compile(str((tm.get("key") or {}).get("shape") or ""))
    except re.error:
        out.append("Its key shape is not a valid pattern.")
    return out


def describe(provider: dict) -> dict:
    """What the wizard's step 5 and Settings show (no key, ever; the prices the cap counts with)."""
    tm = provider["toolsmith_model"]
    return {"id": provider["id"], "company": tm["company"], "hosts": tm["hosts"],
            "models": [{"id": m["id"], "name": m["name"], "prices": effective_prices(provider, m["id"])}
                       for m in tm["models"]],
            "default_model": tm.get("default_model"), "prices_checked": tm.get("prices_checked"),
            "prices_note": tm.get("prices_note", ""), "cost_cap_usd": tm["cost_cap"]["per_build_usd"],
            "consent": tm["consent"], "consent_sha": consent_sha(provider),
            "key": {"name": tm["key"]["name"], "get_it": tm["key"]["get_it"],
                    "vault_name": f"toolsmith.{provider['id']}"},
            "where": f"{tm['company']}, at {', '.join(tm['hosts'])}",
            "problems": provider_problems(provider)}


def cost_usd(prices: dict, usage: dict) -> float:
    """Dollars for one call from its usage count (US dollars per million tokens)."""
    p = _full_prices(prices)
    return (usage.get("in", 0) * p["in"] + usage.get("out", 0) * p["out"]
            + usage.get("cache_read", 0) * p["cache_read"] + usage.get("cache_write", 0) * p["cache_write"]) / 1_000_000


def worst_case_usd(prices: dict, body_bytes: int, max_reply_tokens: int) -> float:
    """The most one call could cost: every body byte a token at the dearest input price, plus the
    full maximum reply length (D52)."""
    p = _full_prices(prices)
    dearest_in = max(p["in"], p["cache_read"], p["cache_write"])
    return (int(body_bytes) * dearest_in + int(max_reply_tokens) * p["out"]) / 1_000_000


# -- the cost ledger, per build ------------------------------------------------------

class Ledger:
    """Tokens and cost per build (``builds/<id>/usage.json``). No content, ever.

    ``reserve`` holds a call's worst case while it is in flight, so two calls
    at once can't both slip under the cap."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._pending = 0.0

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
        return {"total_usd": 0.0, "calls": 0, "at_cap": False, "tokens": {}}

    def reserve(self, usd: float, cap: float) -> bool:
        with self._lock:
            data = self.read()
            if data.get("at_cap") or float(data.get("total_usd") or 0.0) + self._pending + usd > cap:
                return False
            self._pending += usd
            return True

    def release(self, usd: float) -> None:
        with self._lock:
            self._pending = max(0.0, self._pending - usd)

    def add(self, provider: str, model: str, usage: dict | None, usd: float, purpose: str) -> dict:
        with self._lock:
            data = self.read()
            data["calls"] = int(data.get("calls") or 0) + 1
            data["total_usd"] = round(float(data.get("total_usd") or 0.0) + usd, 6)
            if usage is None:
                data["at_cap"] = True        # no usage count: counted as the cap (X29)
            tok = data.setdefault("tokens", {})
            for k, v in (usage or {}).items():
                tok[k] = int(tok.get(k) or 0) + int(v)
            data["last"] = {"provider": provider, "model": model, "purpose": purpose[:120],
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return data


# -- the sender ---------------------------------------------------------------------

class FrontierSender:
    """One build's use of one provider, in the Core. The ``model`` route calls :meth:`turn`."""

    def __init__(self, ports: Any, provider: dict, *, model: str, build_id: str, cap_usd: float,
                 ledger: Ledger, consent_given: str, sleep: Callable[[float], None] = time.sleep,
                 opaque: "OpaqueRecord | None" = None) -> None:
        self.ports, self.provider, self.model = ports, copy.deepcopy(provider), model
        self.tm = self.provider["toolsmith_model"]
        self.company = self.tm["company"]
        self.build_id, self.cap, self.ledger = build_id, float(cap_usd), ledger
        self.consent_given = consent_given
        self._sleep = sleep
        self._handle: str | None = None
        self._clean_cache: dict[str, str] = {}
        self._screened: set[str] = set()
        self.prices = effective_prices(self.provider, model)
        self.opaque = opaque if opaque is not None else OpaqueRecord()

    # what she said yes to, the model and the cap
    def check_ready(self) -> None:
        problems = provider_problems(self.provider)
        if problems:
            raise CloudHeld("provider", problems[0])
        if self.consent_given != consent_sha(self.provider):
            raise CloudHeld("consent", RECONSENT_LINE.format(change=consent_change(self.provider, self.consent_given)
                                                             or CHANGED_LINE.format(company=self.company)))
        if self.prices is None:
            raise CloudHeld("model", f"Pick a {self.company} model with its prices in Settings first.")
        if self.spent() >= self.cap or self.ledger.read().get("at_cap"):
            raise CloudHeld("cap", self.cap_line())

    def cap_line(self) -> str:
        cap = f"{self.cap:.2f}".rstrip("0").rstrip(".")
        return CAP_LINE.replace("{cap}", cap)

    def spent(self) -> float:
        return float(self.ledger.read().get("total_usd") or 0.0)

    def clean(self, text: Any) -> str:
        """Key shapes, personal details and her account name out. Fail closed."""
        text = "" if text is None else str(text)
        if not text:
            return text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in self._clean_cache:
            return self._clean_cache[digest]
        try:
            out = secret_detector.redact_only(text, anywhere=True)
            out = self.ports.screen_outbound(out)
            out = scrub_name(out)
        except Exception:  # noqa: BLE001 - fail closed: nothing is sent
            raise CloudError("privacy_check", PRIVACY_LINE.format(company=self.company)) from None
        self._clean_cache[digest] = out
        self._screened.add(hashlib.sha256(out.encode("utf-8")).hexdigest())
        return out

    def _clean_value(self, v: Any) -> Any:
        """Field names and values alike, all the way down."""
        if isinstance(v, str):
            return self.clean(v)
        if isinstance(v, dict):
            return {self.clean(str(k)): self._clean_value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self._clean_value(x) for x in v]
        return v

    def _clean_messages(self, messages: list[dict]) -> tuple[list[dict], set[str]]:
        """The conversation cleaned, and the strings of the opaque blocks the Core verified.

        Only these block shapes go to a company: user ``text`` and ``tool_result``; assistant
        ``text``, ``tool_use`` and ``opaque`` (verified against the Core's own record)."""
        out: list[dict] = []
        theirs: set[str] = set()
        bad = CloudError("privacy_check", OPAQUE_LINE.format(company=self.company))
        for m in messages:
            role = m.get("role")
            if role not in ("user", "assistant"):
                raise bad
            blocks = []
            for b in m.get("content") or []:
                kind = b.get("type") if isinstance(b, dict) else None
                if role == "user" and kind == "text":
                    blocks.append({"type": "text", "text": self.clean(b.get("text")) or "(empty)"})
                elif role == "user" and kind == "tool_result":
                    c = b.get("content")
                    c = self.clean(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
                    row = {"type": "tool_result", "tool_use_id": self.clean(str(b.get("tool_use_id") or "")),
                           "name": self.clean(str(b.get("name") or "")), "content": c or "(empty)"}
                    if b.get("is_error"):
                        row["is_error"] = True
                    blocks.append(row)
                elif role == "assistant" and kind == "text":
                    blocks.append({"type": "text", "text": self.clean(b.get("text"))})
                elif role == "assistant" and kind == "tool_use":
                    blocks.append({"type": "tool_use", "id": self.clean(str(b.get("id") or "")),
                                   "name": self.clean(str(b.get("name") or "")),
                                   "input": self._clean_value(b.get("input") or {})})
                elif role == "assistant" and kind == "opaque":
                    if not self.opaque.verify(str(b.get("id") or ""), self.provider["id"], self.build_id,
                                              b.get("data")):
                        raise bad
                    theirs.update(_strings(b.get("data")))
                    blocks.append({"type": "opaque", "id": b["id"], "data": b.get("data")})
                else:
                    raise bad
            out.append({"role": role, "content": blocks})
        return out, theirs

    @property
    def purpose(self) -> str:
        return f"build:{self.build_id}"

    def _lend(self) -> str:
        """A lease handle for this provider's key, for its own hosts and this build only (D41)."""
        if self._handle is None:
            try:
                self._handle = self.ports.lend_key(f"toolsmith.{self.provider['id']}", "toolsmith", self.purpose,
                                                   hosts=tuple(self.tm["hosts"]), job=self.purpose)
            except KeyUnavailable as exc:
                if exc.reason == "locked":
                    raise CloudWaitVault("locked", VAULT_LINE) from None
                if exc.reason == "missing":
                    raise CloudHeld("key", NO_KEY_LINE.format(company=self.company)) from None
                raise CloudHeld("key", str(exc)) from None
        return self._handle

    def _key(self, host: str) -> str:
        """The key bytes for one send to *host*, from the lease, at the moment of sending."""
        capped = False
        for again in (False, True):
            try:
                handle = self._lend()
            except CloudWaitVault:
                if capped:                              # its 3 hours are up and the vault is locked: say why
                    raise CloudWaitVault("locked", LEASE_CAP_LINE) from None
                raise
            try:
                return self.ports.key_value(handle, borrower="toolsmith", purpose=self.purpose, host=host)
            except KeyUnavailable as exc:
                if exc.reason in ("ended", "cap"):
                    self._handle = None             # Archivist ended it already
                else:
                    self._end_lease()               # a refused or odd lease ends now, not at its cap
                capped = exc.reason == "cap"
                if exc.reason in ("ended", "cap") and not again:
                    continue                            # the lease ran out: borrow it once more
                if exc.reason == "locked":
                    raise CloudWaitVault("locked", VAULT_LINE) from None
                raise CloudHeld("key", NO_KEY_LINE.format(company=self.company)) from None
        raise CloudHeld("key", NO_KEY_LINE.format(company=self.company))

    def leased(self) -> bool:
        """Does this build hold a live lease for its key now (D68)? Never reads the key bytes."""
        handle = self._handle
        if handle is None:
            return False
        live = getattr(self.ports, "lease_live", None)
        if not callable(live):
            return True
        try:
            return bool(live(handle))
        except Exception:  # noqa: BLE001 - can't tell: not leased
            return False

    def probe(self) -> bool:
        """Can the key be lent now (the vault-wait poll, and her Yes, D68)? Never reads the key bytes. A handle
        whose lease ended (a hand lock, its 3 hours) is let go and borrowed again."""
        if self._handle is not None and not self.leased():
            self._end_lease()                           # already ended at Archivist: this only lets it go
        self._lend()
        return True

    def _end_lease(self) -> None:
        """End the lease at Archivist, then forget the handle (never just forget it)."""
        handle, self._handle = self._handle, None
        if handle:
            try:
                self.ports.end_lease(handle)
            except Exception:  # noqa: BLE001 - Archivist also ends it at its 3 hour cap
                pass

    def close(self) -> None:
        """The build ended: its key lease ends too (D41)."""
        self._end_lease()

    def _final_screen(self, text: str) -> bool:
        """Does this string of the final wire body pass the key screen and the outbound screen?"""
        if secret_detector.redact_only(text, anywhere=True) != text:
            return False
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in self._screened:
            return True
        try:
            ok = self.ports.screen_outbound(text) == text
        except Exception:  # noqa: BLE001 - no screen, no send
            return False
        if ok:
            self._screened.add(digest)
        return ok

    def _wire_request(self, req: dict, theirs: set[str]) -> tuple[str, dict, bytes]:
        built = wire.build_request(self.provider, req)
        url, headers, body = built["url"], dict(built["headers"]), built["body"]
        parts = urllib.parse.urlsplit(url)
        listed = known_hosts().get(self.company, [])
        if parts.scheme != "https" or parts.hostname not in self.tm["hosts"] or parts.hostname not in listed:
            raise CloudError("host", f"{self.company} tried to reach more than Toolsmith may. Turned off.")
        key_header = self.tm["key"]["header"]
        for name, value in headers.items():
            if "{key}" in str(value) and name.lower() != key_header.lower():
                raise CloudError("host", f"{self.company} tried to reach more than Toolsmith may. Turned off.")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_BODY_BYTES:
            raise CloudError("too_big", "This app's notes grew too long for one try.")
        # The key screen AND the outbound screen once more on the final wire body, field
        # names and values (09 section 4, X29). Only a string from one of the company's own
        # returned items, verified against the Core's record, did not come from here.
        for text in _strings(body):
            if text not in theirs and not self._final_screen(text):
                raise CloudError("privacy_check", PRIVACY_LINE.format(company=self.company))
        return url, headers, data

    def turn(self, system: str, messages: list[dict], tools: list[dict], *,
             max_tokens: int = MAX_TOKENS, purpose: str = "build") -> dict:
        """One call: ``{content, stop, usage}`` (neutral blocks). Raises CloudError kinds."""
        self.check_ready()
        try:
            max_tokens = max(1, min(int(max_tokens or MAX_TOKENS), MAX_TOKENS))
        except (TypeError, ValueError):
            max_tokens = MAX_TOKENS
        cleaned, theirs = self._clean_messages(messages)
        req = {"model": self.model, "system": self.clean(system), "tools": tools,
               "messages": cleaned, "max_tokens": max_tokens, "purpose": str(purpose or "build")[:120]}
        url, headers, data = self._wire_request(req, theirs)
        estimate = worst_case_usd(self.prices, len(data), max_tokens)
        if not self.ledger.reserve(estimate, self.cap):
            raise CloudHeld("cap", self.cap_line())
        try:
            return self._send(url, headers, data, purpose)
        finally:
            self.ledger.release(estimate)

    def _send(self, url: str, headers: dict, data: bytes, purpose: str) -> dict:
        key_header = self.tm["key"]["header"]
        host = urllib.parse.urlsplit(url).hostname or ""
        for attempt in range(RETRIES + 1):
            sent = {k: (str(v).replace("{key}", self._key(host)) if k.lower() == key_header.lower() else v)
                    for k, v in headers.items()}
            try:
                status, rh, raw = self.ports.http_send("POST", url, subject="toolsmith:cloud", headers=sent,
                                                       body=data, timeout=CALL_TIMEOUT)
            except Redirected:
                raise CloudError("redirect", REDIRECT_LINE.format(company=self.company)) from None
            except Refused as exc:
                raise CloudError("bridge", str(exc)) from None
            except NotBuilt as exc:
                raise CloudError("not_built", str(exc)) from None
            except OSError:
                raise CloudOffline("offline", OFFLINE_LINE.format(company=self.company)) from None
            finally:
                sent = {}
            got = wire.read_reply(self.tm["wire"], {"status": status, "headers": rh, "body": raw})
            err = got.get("error")
            if err == "busy" and attempt < RETRIES:
                self._sleep(_wait(got.get("retry_after_s"), attempt))
                continue
            if err == "key":
                self._end_lease()                   # a key the company refused: its lease ends now
                raise CloudError("key", KEY_LINE.format(company=self.company))
            if err == "busy":
                raise CloudError("busy", f"{self.company} is busy right now. Toolsmith stopped this try.")
            if err == "too_big":
                raise CloudError("too_big", "This app's notes grew too long for one try.")
            if err == "refused":
                raise CloudError("refused", f"{self.company} could not take that request (it answered {status}).")
            if err:
                raise CloudError("odd_reply", f"{self.company} gave a reply Toolsmith could not read.")
            usage = got.get("usage")
            usd = cost_usd(self.prices, usage) if usage else 0.0
            self.ledger.add(self.provider["id"], self.model, usage, usd, purpose)
            got["cost_usd"] = usd                   # for Toolsmith's count of a job's share (D119); no model reads it
            for b in got.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "opaque":
                    b["id"] = self.opaque.keep(self.provider["id"], self.build_id, b.get("data"))
            return got
        raise CloudError("busy", f"{self.company} is busy right now. Toolsmith stopped this try.")


class OpaqueRecord:
    """The company items the Core itself received, per provider and build: id -> HMAC of the exact bytes.

    The HMAC key and the record never leave the Core process."""

    def __init__(self) -> None:
        self._key = secrets.token_bytes(32)
        self._seen: dict[str, tuple[str, str, str]] = {}
        self._lock = threading.Lock()

    def _mac(self, provider: str, build: str, data: Any) -> str:
        raw = json.dumps([provider, build, data], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hmac.new(self._key, raw.encode("utf-8"), hashlib.sha256).hexdigest()

    def keep(self, provider: str, build: str, data: Any) -> str:
        oid = "op_" + secrets.token_hex(12)
        with self._lock:
            self._seen[oid] = (provider, build, self._mac(provider, build, data))
        return oid

    def verify(self, oid: str, provider: str, build: str, data: Any) -> bool:
        with self._lock:
            got = self._seen.get(oid)
        if got is None or got[0] != provider or got[1] != build:
            return False
        return hmac.compare_digest(got[2], self._mac(provider, build, data))


def _strings(node: Any) -> list[str]:
    """Every string in a JSON body: field names and values."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [x for k, v in node.items() for x in [str(k), *_strings(v)]]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return []


def _wait(after: Any, attempt: int) -> float:
    try:
        wait = float(after)
    except (TypeError, ValueError):
        wait = min(MAX_WAIT_S, 2.0 * (2 ** attempt)) + random.uniform(0, 1)
    return max(0.5, min(MAX_WAIT_S, wait))


__all__ = ["BUILTIN_DIR", "RESERVED_IDS", "CAP_LINE", "OPAQUE_LINE", "OpaqueRecord", "FIND_MAX_TOKENS", "FrontierSender", "KEY_LINE", "Ledger", "MAX_TOKENS",
           "OFFLINE_LINE", "SENT_ITEMS", "builtin_ids", "builtin_providers", "consent_change", "consent_sha", "core_prices",
           "cost_usd", "describe", "effective_prices", "installed_providers", "known_hosts", "load_providers", "provider_problems",
           "worst_case_usd"]
