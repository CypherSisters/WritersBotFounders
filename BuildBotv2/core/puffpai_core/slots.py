"""The slot table (docs/zones/ZONES_TOGETHER.md section 1, plus zone3.toolsmith-model from D35).

``SLOTS[name] = Slot(contract, protocol, how_many)``. ``protocol`` is None for
slots marked "later" (speech and vision). The manifest schema's slot list must
match these names (a test holds them together).
"""

from __future__ import annotations

from typing import NamedTuple

from .zone1.slots import Detector, LabelPack, Retention, VaultBackup, VaultStore, VaultUnlock
from .zone2.slots import Embedder, Reranker, Runtime
from .zone3.slots import BridgeEnforcer, NetMonitor, NetworkDevice, PolicyFeed, ToolsmithModel


class Slot(NamedTuple):
    contract: int
    protocol: type | None
    how_many: str          # "one" | "zero_or_one" | "many"


SLOTS: dict[str, Slot] = {
    "zone1.detector": Slot(1, Detector, "many"),
    "zone1.label-pack": Slot(1, LabelPack, "many"),
    "zone1.retention": Slot(1, Retention, "many"),
    "zone1.vault-store": Slot(1, VaultStore, "one"),
    "zone1.vault-unlock": Slot(1, VaultUnlock, "many"),
    "zone1.vault-backup": Slot(1, VaultBackup, "many"),
    "zone2.runtime": Slot(1, Runtime, "one"),
    "zone2.embedder": Slot(1, Embedder, "one"),
    "zone2.reranker": Slot(1, Reranker, "zero_or_one"),
    "zone2.speech-in": Slot(1, None, "one"),
    "zone2.speech-out": Slot(1, None, "one"),
    "zone2.vision": Slot(1, None, "one"),
    "zone3.bridge-enforcer": Slot(1, BridgeEnforcer, "many"),
    "zone3.network-device": Slot(1, NetworkDevice, "many"),
    "zone3.net-monitor": Slot(1, NetMonitor, "many"),
    "zone3.policy-feed": Slot(1, PolicyFeed, "many"),
    "zone3.toolsmith-model": Slot(1, ToolsmithModel, "many"),
}

#: Slots whose provider sees real values (D31(c)); no network, token or keys (X13).
RAW_READER_SLOTS = frozenset({"zone2.runtime", "zone2.speech-in", "zone2.speech-out", "zone2.vision"})
