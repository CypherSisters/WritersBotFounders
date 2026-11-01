# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/vault_inbox.py (adapted: ensure_inbox :226,
# deposit :278, drain :354; Fernet inside the box became AES-256-GCM; one box per item; named secrets too)
"""The vault inbox: write-only, works while the vault is locked (D36(a), D28(17), Z1V 6).

A secret typed before its home is ready (the vault locked, or not set up yet) is
sealed here and filed later, never dropped (v1 bug PB-001). Held values found
while the vault is locked (chat, files) come here too, under the ref their
handle already names, so saved text stays valid after filing.

Shape: a sealed box (X25519 + HKDF-SHA256 + AES-256-GCM). The inbox private key
lives inside the vault as a reserved item; its public key sits in the keyring
file. Sealing needs only the public key. Each box is one file
``<state>/vault/inbox/<ms>-<rand>.box``; filing opens each box with the private
key, writes the item into the vault, then deletes the box.

Before the vault is set up there is no inbox key yet. Then items wait in this
process's memory only and are filed by setup. HONEST: a Core restart before
setup loses them, and the caller is told so (``seal`` returns ``kept="memory"``).

Honest limits (Windows, same user): anyone who can read the public key can seal
a box (that is what makes it write-only); fake boxes are filed as data and never
acted on. So filing never replaces anything already saved: a box for a key name
that is already in the vault waits, sealed, until the owner says Yes with her
PIN (floor-level, D29; ``Archivist.file_replacing``), and a held-value box whose
ref is already in use is set aside. A program running as the owner's Windows user can swap the public key
so later boxes go to a key it holds, until the next unlock notices the swap
(the unlock compares it with the copy sealed in the vault and reports boxes it
cannot open). Leaks: how many boxes wait and when each was sealed.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from . import crypto

PRIVATE_NAME = "__inbox.private"
PUB_SHA_NAME = "__inbox.pub_sha"
MAX_ITEM_BYTES = 64 * 1024
MAX_PENDING = 2000
_SUFFIX = ".box"


def _raw_pub(key: X25519PublicKey) -> bytes:
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def _box_key(shared: bytes, eph: bytes, recipient: bytes) -> bytes:
    return crypto.hkdf(shared, b"pz1/inbox" + eph + recipient)


class Inbox:
    """Sealed deposits for one vault. Archivist files them when the vault opens."""

    def __init__(self, vault: Any) -> None:
        self.vault = vault
        self.dir = vault.dir / "inbox"
        self._memory: list[dict] = []           # before setup only (see module note)
        self._lock = threading.Lock()
        self.set_aside = 0

    # -- keys -----------------------------------------------------------------
    def ensure_key(self) -> None:
        """Make (or check) the inbox key pair. Vault must be open."""
        v = self.vault
        ring = v._read_keyring()
        pub_hex = (ring.get("inbox") or {}).get("pub")
        try:
            sealed_sha = v.get(PUB_SHA_NAME).decode("ascii")
            have_private = True
            v.get(PRIVATE_NAME)
        except Exception:
            sealed_sha, have_private = None, False
        if pub_hex and have_private and sealed_sha == crypto.sha256(bytes.fromhex(pub_hex)):
            return
        priv = X25519PrivateKey.generate()
        raw_priv = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        pub = _raw_pub(priv.public_key())
        v.put(PRIVATE_NAME, raw_priv, kind="reserved", label="secret.private_key", tier="P4", reserved=True)
        v.put(PUB_SHA_NAME, crypto.sha256(pub).encode("ascii"), kind="reserved", label="secret.token", tier="P4",
              reserved=True)
        ring = v._read_keyring()
        ring["inbox"] = {"pub": pub.hex()}
        v._write_keyring(ring, v._dek)
        if pub_hex:
            v.audit("inbox_key_replaced")

    def _public(self) -> bytes | None:
        try:
            ring = self.vault._read_keyring()
        except Exception:
            return None
        pub = (ring.get("inbox") or {}).get("pub")
        try:
            return bytes.fromhex(pub) if pub else None
        except ValueError:
            return None

    # -- seal -----------------------------------------------------------------------
    def _deposit(self, item: dict) -> str:
        body = json.dumps(item, sort_keys=True).encode("utf-8")
        if len(body) > MAX_ITEM_BYTES:
            raise ValueError("too big for the inbox")
        if not self.vault.is_set_up():
            with self._lock:
                if len(self._memory) >= MAX_PENDING:
                    raise ValueError("inbox full")
                self._memory.append(item)
            return "memory"
        recipient = self._public()
        if recipient is None:
            raise ValueError("no inbox key")
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.pending() >= MAX_PENDING:
            raise ValueError("inbox full")
        eph = X25519PrivateKey.generate()
        eph_pub = _raw_pub(eph.public_key())
        key = _box_key(eph.exchange(X25519PublicKey.from_public_bytes(recipient)), eph_pub, recipient)
        box_id = f"{int(time.time() * 1000):013d}-{secrets.token_hex(6)}"
        nonce = os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, body, f"pz1/inbox|{self.vault._vid()}|{box_id}".encode())
        data = json.dumps({"to": crypto.sha256(recipient), "eph": eph_pub.hex(), "nonce": nonce.hex(),
                           "ct": ct.hex()}).encode("utf-8")
        tmp = self.dir / (box_id + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, self.dir / (box_id + _SUFFIX))
        return "inbox"

    def seal(self, name: str, value: str | bytes, *, label: str = "secret.api_key", tier: str = "P4") -> dict:
        """A named secret whose home isn't ready. ``{"kept": "inbox"|"memory"}``; never dropped silently."""
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        kept = self._deposit({"type": "secret", "name": name, "label": label, "tier": tier, "value": raw.hex(),
                              "sealed_at": int(time.time())})
        self.vault.audit("inbox_sealed", name=name, kept=kept)
        return {"kept": kept}

    def seal_held(self, ref: str, value: str, *, label: str, tier: str, source_ref: str | None,
                  expire_days: int | None) -> str:
        kept = self._deposit({"type": "held", "ref": ref, "label": label, "tier": tier, "value": value,
                              "source_ref": source_ref, "expire_days": expire_days, "sealed_at": int(time.time())})
        self.vault.audit("inbox_sealed", name="held", kept=kept, label=label)
        return kept

    def pending(self) -> int:
        try:
            return len([p for p in self.dir.glob("*" + _SUFFIX)]) + len(self._memory)
        except OSError:
            return len(self._memory)

    # -- file (vault open) ----------------------------------------------------------------
    def drain(self) -> list[dict]:
        """Open every box (vault open) and return the items; each box is removed only after its item is returned
        to the caller, who files it. Boxes sealed to another key, or that won't open, are set aside."""
        v = self.vault
        with self._lock:
            items, self._memory = list(self._memory), []
        if not self.dir.is_dir():
            return items
        try:
            raw_priv = v.get(PRIVATE_NAME)
        except Exception:
            return items
        priv = X25519PrivateKey.from_private_bytes(raw_priv)
        mine = crypto.sha256(_raw_pub(priv.public_key()))
        for path in sorted(self.dir.glob("*" + _SUFFIX)):
            box_id = path.name[:-len(_SUFFIX)]
            try:
                box = json.loads(path.read_text(encoding="utf-8"))
                if box.get("to") != mine:
                    raise ValueError("wrong key")
                eph = bytes.fromhex(box["eph"])
                key = _box_key(priv.exchange(X25519PublicKey.from_public_bytes(eph)), eph, _raw_pub(priv.public_key()))
                body = AESGCM(key).decrypt(bytes.fromhex(box["nonce"]), bytes.fromhex(box["ct"]),
                                           f"pz1/inbox|{v._vid()}|{box_id}".encode())
                item = json.loads(body)
                if len(body) > MAX_ITEM_BYTES or item.get("type") not in ("secret", "held"):
                    raise ValueError("bad item")
            except (OSError, ValueError, KeyError, InvalidTag):
                aside = self.dir / "set_aside"
                aside.mkdir(exist_ok=True)
                try:
                    os.replace(path, aside / path.name)
                except OSError:
                    pass
                self.set_aside += 1
                v.audit("inbox_set_aside")
                continue
            item["_box"] = str(path)
            items.append(item)
        return items

    def keep(self, item: dict) -> None:
        """Leave *item* waiting: a box stays on disk as it is; an item from memory (before setup) goes back."""
        if not item.get("_box"):
            with self._lock:
                self._memory.append({k: v for k, v in item.items() if k != "_box"})

    def set_aside_item(self, item: dict) -> None:
        """An item that opened but must not be filed (a bad name, a ref already in use): moved aside."""
        box = item.get("_box")
        if box:
            aside = self.dir / "set_aside"
            aside.mkdir(exist_ok=True)
            try:
                os.replace(box, aside / Path(box).name)
            except OSError:
                pass
        self.set_aside += 1
        self.vault.audit("inbox_set_aside")

    @staticmethod
    def done(item: dict) -> None:
        box = item.get("_box")
        if box:
            try:
                Path(box).unlink()
            except OSError:
                pass
