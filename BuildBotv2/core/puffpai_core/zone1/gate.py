"""The gate before Zone 2 (X6, D25, D31(b), ZONES_TOGETHER "1 -> 2").

``prepare(text, vault_open, *, archivist=None, path="chat", local_runtime=True, people=None, source_ref=None)
-> (text_for_model, handles)``

For every labelled span in *text*:
- keys, tokens and passwords (``secret.*``, ``auth.password``): the value is held
  (vault, or the sealed inbox while locked) and the brain sees only its handle
  ``{{vault:<ref>}} (an API key)``, vault open or not (D31(b));
- ``keep: none`` labels (one-time codes, card security codes): scrubbed, never
  stored, never shown ("[a one-time code removed]");
- other held values (P2 and up by default): held; the local runtime on this
  computer gets the real value for this turn only while the vault is open and
  ``local_runtime`` is true (D25); otherwise the handle;
- open labels (``person.name``): left as typed, as in v1.

``handles`` is a list of ``Handle`` rows (ref, label, tier, span, shown_to_model,
kept) with ``handles.store_text``: the version for plain stores (chat store,
index, logs), which holds handles and kinds, never values (Z1C F7).

Fail closed: if the floor check can't run, or no Archivist is bound, ``prepare``
raises ``GateClosed`` and the caller (Concierge's turn hook, lane L2) must not
send or save the turn. ``vault_open`` is the caller's view; the gate also asks
the vault itself and uses the stricter answer.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from . import labels
from .classify import Classified, FloorError, classify_text

_bound: Any = None


class GateClosed(Exception):
    """The privacy check could not run, so this was not sent or saved."""


class OwnWords(str):
    """A Core-made string from a document she marked "no vault" (D146): read as an ordinary file."""


def bind(archivist: Any) -> None:
    """The Core binds its one Archivist at start (``archivist.for_state``)."""
    global _bound
    _bound = archivist


def vault_open() -> bool:
    """The bound Archivist's answer (L2's ``zone2.gate.production`` reads it); no Archivist or unsure: locked."""
    try:
        return bool(_bound is not None and _bound.is_open())
    except Exception:
        return False


def handle_text(ref: str, label: str) -> str:
    return "{{vault:" + ref + "}} (" + labels.words(label) + ")"


def scrub_text(label: str) -> str:
    return "[" + labels.words(label) + " removed]"


@dataclass(frozen=True)
class Handle:
    ref: str | None
    label: str
    labels: tuple[str, ...]
    tier: str
    start: int
    end: int
    shown_to_model: bool
    kept: str                 # vault | inbox | scrubbed


class Handles(list):
    store_text: str = ""
    top_tier: str = "P0"
    checked: str = "full"
    classified: Classified | None = None


def prepare(text: str, vault_open: bool, *, archivist: Any = None, path: str = "chat", local_runtime: bool = True,
            people: list[dict] | None = None, source_ref: str | None = None) -> tuple[str, Handles]:
    arch = archivist or _bound
    if arch is None:
        raise GateClosed("No Archivist is running, so nothing private can be kept.")
    try:
        found = classify_text(text, path=path, people=people)
    except FloorError:
        raise GateClosed("The privacy check could not run, so this was not sent or saved.") from None
    try:
        really_open = bool(vault_open) and arch.is_open() and bool(local_runtime)
    except Exception:
        really_open = False
    model_parts: list[str] = []
    store_parts: list[str] = []
    out = Handles()
    at = 0
    kept_any = "not_kept"
    for g in found.groups():
        if g.start < at:
            continue
        value = text[g.start:g.end]
        label = g.primary
        if g.scrub:
            rep_model = rep_store = scrub_text(next(x for x in g.labels if labels.effects(x).get("keep") == "none"))
            h = Handle(None, label, tuple(g.labels), g.tier, g.start, g.end, False, "scrubbed")
            kept_any = "scrubbed" if kept_any == "not_kept" else kept_any
        elif g.secret or g.hold:
            if g.secret:
                label = next(x for x in g.labels if labels.is_secret(x))
            try:
                was_open = arch.is_open()
                ref = arch.hold(value, label, source_ref=source_ref, tier=g.tier)
            except Exception:
                raise GateClosed("A private detail could not be kept safe, so this was not sent or saved.") from None
            kept = "vault" if was_open else "inbox"
            kept_any = kept if kept_any in ("not_kept", "scrubbed") else kept_any
            rep_store = handle_text(ref, label)
            shown = really_open and not g.secret
            rep_model = value if shown else rep_store
            h = Handle(ref, label, tuple(g.labels), g.tier, g.start, g.end, shown, kept)
        else:
            continue                            # open labels stay as typed
        model_parts.append(text[at:g.start] + rep_model)
        store_parts.append(text[at:g.start] + rep_store)
        at = g.end
        out.append(h)
    model_parts.append(text[at:])
    store_parts.append(text[at:])
    out.store_text = "".join(store_parts)
    out.top_tier = found.top_tier
    out.checked = found.checked
    out.classified = found
    _publish_labeled(arch, found, out, path, kept_any, source_ref)
    return "".join(model_parts), out


def _publish_labeled(arch: Any, found: Classified, handles: Handles, path: str, kept: str,
                     source_ref: str | None) -> None:
    if not found.hits or getattr(arch, "bus", None) is None:
        return
    from ..bus import Bus
    kind = {"chat": "chat_turn", "files": "file_chunk", "package_data": "package_data", "outbound": "outbound"}[path]
    payload = {"item": {"kind": kind, "path": path, "ref": source_ref or ("t" + secrets.token_hex(8))},
               "labels": found.label_rows(), "top_tier": found.top_tier,
               "handles": [h.ref for h in handles if h.ref], "kept": kept, "checked": found.checked}
    arch.bus.publish(Bus.message("data.labeled", payload, {"kind": "agent", "id": "archivist"}))
