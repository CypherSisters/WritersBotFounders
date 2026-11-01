"""Which provider fills each zone slot (``GET /v1/providers``, ``PUT /v1/providers/{slot}``).

Installed providers come from Registry rows with ``provides``. For a slot that
holds one at a time (``one`` or ``zero_or_one`` in ``slots.SLOTS``) the owner
picks the active one; the pick raises a Yes card (scope ``provider_swap``) and
is written to ``settings\\providers.json`` only after her Yes, then the Core
publishes ``provider.changed``. Moving the vault (``zone1.vault-store``) has its
own route, ``/v1/vault/store/move`` (lane L3), because it copies data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..bus import Bus, Source
from ..consent import ConsentStore
from ..registry import Registry
from ..slots import SLOTS
from .manager import AddError

CORE = Source("core", "core:providers")


class Providers:
    def __init__(self, state: Path | str, registry: Registry, consent: ConsentStore, bus: Bus) -> None:
        self.path = Path(state) / "settings" / "providers.json"
        self.registry = registry
        self.consent = consent
        self.bus = bus
        self._pending: dict[str, tuple[str, str]] = {}
        consent.on_answer(self._answered)

    def active(self) -> dict[str, str]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def list(self) -> list[dict]:
        rows = self.registry.list(kind="provider")
        active = self.active()
        out = []
        for slot, spec in SLOTS.items():
            ids = [r["id"] for r in rows if any(p["slot"] == slot for p in r.get("provides", []))]
            one = spec.how_many in ("one", "zero_or_one")
            out.append({"slot": slot, "contract": spec.contract, "how_many": spec.how_many, "installed": ids,
                        "active": (active.get(slot) if one else ids)})
        return out

    def pick(self, slot: str, id: str) -> dict:
        spec = SLOTS.get(slot)
        if spec is None:
            raise AddError(f"There is no slot {slot!r}.", 404)
        if slot == "zone1.vault-store":
            raise AddError("Moving the vault copies your data; use /v1/vault/store/move.", 409)
        if spec.how_many == "many":
            raise AddError(f"{slot} takes many providers at once; there is nothing to pick.", 409)
        row = self.registry.get(id)
        if row is None or not any(p["slot"] == slot for p in row.get("provides", [])):
            raise AddError(f"{id!r} is not an installed provider for {slot}.", 404)
        card = {"title": f"Use {row['name']} for {slot}?", "slot": slot, "id": id, "name": row["name"],
                "from": self.active().get(slot)}
        cid = self.consent.request({"kind": "provider_swap", "target": id}, card, False)
        self._pending[cid] = (slot, id)
        from ..consent import card_sha
        return {"consent_id": cid, "card": card, "card_sha": card_sha(card)}

    def recover_after_restart(self) -> list[str]:
        """At Core start: provider-swap cards left from the last run expire (their plan was in memory)."""
        from .manager import EXPIRED_LINE, _expire
        gone = []
        for record in self.consent.list(status="pending"):
            if record["scope"].get("kind") == "provider_swap" and record["consent_id"] not in self._pending:
                _expire(self.consent, record["consent_id"], EXPIRED_LINE)
                gone.append(record["consent_id"])
        return gone

    def _answered(self, record: dict) -> None:
        pick = self._pending.pop(record["consent_id"], None)
        if pick is None or record["status"] != "granted":
            return
        slot, id = pick
        active = self.active()
        before = active.get(slot)
        active[slot] = id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(active, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        self.bus.publish(Bus.message("provider.changed", {"slot": slot, "from": before, "to": id,
                                                          "migration": "none"}, CORE))


__all__ = ["Providers"]
