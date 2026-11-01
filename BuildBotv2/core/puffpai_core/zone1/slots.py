"""Zone 1 slot contracts (contract 1), as the Core sees them.

Each Protocol is the Core-side face of a slot: a process provider answers
these operations as MCP tools (``audience: "core"``, never shown to the
brain); a data provider (label pack, retention, ``rules`` detector) is a JSON
pack the Core loads into an object with this face. Sources:
``docs/zones/Z1_VAULT_PROVIDERS.md`` section 4, ``docs/zones/Z1_CLASSIFY_AND_LABEL.md``
section 3, names from ``docs/zones/ZONES_TOGETHER.md`` section 1.

Errors are returned, not raised, as ``{"error": "<code>", ...}`` with the
codes each operation lists. Blobs travel base64 in MCP JSON, at most 4 MiB.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Result = dict[str, Any]


@runtime_checkable
class VaultStore(Protocol):
    """``zone1.vault-store``: where sealed blobs live. Exactly one active. Sees ciphertext only.

    ``put`` is atomic. ``not_found`` and ``unavailable`` are never mixed up:
    a missing drive is never an empty vault. The root record is blob id ``root``.
    """

    def info(self) -> Result: ...                       # {contract, location_text, remote, max_blob_bytes}
    def health(self) -> Result: ...                     # {ok, reason}
    def put(self, id: str, version: int, blob: bytes, expect_version: int | None) -> Result: ...
    # -> {ok} | conflict{current} | full | unavailable{reason} | refused{reason}
    def get(self, id: str) -> Result: ...               # {version, blob} | not_found | unavailable
    def list(self, after: str | None = None) -> Result: ...   # {items: [{id, version, size}], next} | unavailable
    def delete(self, id: str, expect_version: int | None) -> Result: ...  # {ok} | not_found | conflict | unavailable


@runtime_checkable
class VaultUnlock(Protocol):
    """``zone1.vault-unlock``: an extra way in. Many at once; community ones are AND only (X2).

    Turns a device ceremony into 32 bytes for a Core challenge. PIN entry never
    goes through a provider. ``derive`` is deterministic per (handle, challenge).
    """

    def info(self) -> Result: ...                       # {factor: hardware_key|platform|phone, label, needs_touch}
    def enroll(self, vault_id: str, challenge: bytes) -> Result: ...   # {handle, output} | not_present | cancelled | unavailable
    def derive(self, handle: str, challenge: bytes) -> Result: ...     # {output} | not_present | cancelled | unavailable
    def forget(self, handle: str) -> Result: ...        # {ok}


@runtime_checkable
class VaultBackup(Protocol):
    """``zone1.vault-backup``: sealed copies elsewhere. Zero or more. Ciphertext only."""

    def info(self) -> Result: ...
    def health(self) -> Result: ...
    def write(self, bundle_id: str, data: bytes) -> Result: ...
    def list(self) -> Result: ...
    def read(self, bundle_id: str) -> Result: ...
    def delete(self, bundle_id: str) -> Result: ...


@runtime_checkable
class Detector(Protocol):
    """``zone1.detector``, process form (a raw reader, X13), over stdio JSON lines.

    ``hello`` must match the manifest's ``detector.emits`` or the detector is off.
    ``scan`` returns spans only, never text; the Core recomputes the value from
    the offsets and drops anything outside the text or not in ``emits``.
    ``path`` is a word (chat | files | package_data | outbound), never a file path.
    """

    def hello(self) -> Result: ...                      # {emits: [label], version}
    def scan(self, id: str, path: str, lang: str, text: str, labels_wanted: list[str]) -> Result: ...
    # -> {id, hits: [{start, end, label, confidence}]} | {id, error: too_long|unsupported|busy}


@runtime_checkable
class LabelPack(Protocol):
    """``zone1.label-pack``: data only. Packs stack and may only make labels stricter."""

    id: str

    def labels(self) -> list[dict]: ...    # [{id, words, tier, effects: {hold, index, cloud, share, expire_days, keep}}]
    def rules(self) -> list[dict]: ...     # rule detectors: terms | regex | near | schema | people | brain
    def combine(self) -> list[dict]: ...   # [{any_of: [label], at_least, label}]


@runtime_checkable
class Retention(Protocol):
    """``zone1.retention``: data only, run by Archivist. The shorter expiry wins."""

    id: str

    def expiry(self) -> dict[str, int]: ...             # label id -> expire_days
    def forget_turns_after_days(self) -> int | None: ...


__all__ = ["Detector", "LabelPack", "Retention", "VaultBackup", "VaultStore", "VaultUnlock"]
