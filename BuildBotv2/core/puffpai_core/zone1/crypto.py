"""The Core's only crypto for Zone 1 (Z1V section 3). Providers never see any of this.

One algorithm today, ``aes-256-gcm-hkdf/1``: every blob write derives a fresh
subkey ``HKDF-SHA256(DEK, salt=32 random bytes, info="pz1/blob" + vault_id)``
and seals with AES-256-GCM (96-bit random nonce, 128-bit tag). A fresh subkey
per write means a repeated random nonce can never repeat under one key (F3).
The AAD binds schema, vault id, blob id, blob version and algorithm (F4), so a
store that swaps two blobs or serves an old version gets ``Tampered``.

PIN and recovery-code wraps use Argon2id at or above m=64 MiB, t=3, p=1 (F5).
The Core refuses to write a wrap below that floor, and reads only wraps whose
parameters are at or above it. Unknown ``alg_id`` is refused on read and write (F2).

New ciphers or KDFs arrive only with a Core update, from the ``cryptography``
library. No package code is ever imported here.

Honest limit (Windows, same user): keys live in this process's memory while
the vault is open or a lease is live. Python cannot reliably wipe memory;
``bytearray`` buffers are zeroed on lock as best effort only. A program
running as the owner's Windows user can read this process's memory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALG_ID = "aes-256-gcm-hkdf/1"
ALGS = frozenset({ALG_ID})
SCHEMA = "pz1"
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 32
_MAGIC = b"PZ1\x01"

#: Argon2id floor (F5). memory_cost is in KiB.
KDF_FLOOR = {"alg": "argon2id", "m": 64 * 1024, "t": 3, "p": 1}
KDF_DEFAULT = dict(KDF_FLOOR)

#: Size padding steps (Z1V 3): 256 B, 4 KB, 64 KB, then 1 MB steps.
_PAD_STEPS = (256, 4096, 65536)
_MB = 1024 * 1024


class CryptoError(Exception):
    """Base for Zone 1 crypto errors. Messages never carry key or plaintext bytes."""


class Tampered(CryptoError):
    """A blob or wrap failed its tag check, or its binding does not match."""


class UnknownAlgorithm(CryptoError):
    """An ``alg_id`` not on the Core's list (F2)."""


class WeakKdf(CryptoError):
    """A KDF below the floor (F5)."""


def new_key() -> bytearray:
    return bytearray(os.urandom(KEY_BYTES))


def zero(buf: bytearray | None) -> None:
    """Best effort: Python may hold other copies (see the module note)."""
    if buf is None:
        return
    for i in range(len(buf)):
        buf[i] = 0


def hkdf(key: bytes | bytearray, info: bytes, salt: bytes | None = None, length: int = KEY_BYTES) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(bytes(key))


def blob_aad(vault_id: str, blob_id: str, version: int, alg: str = ALG_ID) -> bytes:
    return json.dumps({"schema": SCHEMA, "vault_id": vault_id, "blob_id": blob_id,
                       "blob_version": int(version), "alg_id": alg},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


def pad(data: bytes) -> bytes:
    """Length-prefixed, zero-padded to the next size step."""
    body = struct.pack(">I", len(data)) + data
    n = len(body)
    for step in _PAD_STEPS:
        if n <= step:
            return body + b"\x00" * (step - n)
    total = ((n + _MB - 1) // _MB) * _MB
    return body + b"\x00" * (total - n)


def unpad(data: bytes) -> bytes:
    if len(data) < 4:
        raise Tampered("padding")
    (n,) = struct.unpack(">I", data[:4])
    if n > len(data) - 4:
        raise Tampered("padding")
    return data[4:4 + n]


def seal(dek: bytes | bytearray, vault_id: str, blob_id: str, version: int, plaintext: bytes,
         alg: str = ALG_ID) -> bytes:
    """Seal one blob. Output: magic, alg length + alg, salt, nonce, ciphertext+tag."""
    if alg not in ALGS:
        raise UnknownAlgorithm(alg)
    salt = os.urandom(SALT_BYTES)
    sub = hkdf(dek, b"pz1/blob" + vault_id.encode("utf-8"), salt=salt)
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(sub).encrypt(nonce, pad(plaintext), blob_aad(vault_id, blob_id, version, alg))
    a = alg.encode("ascii")
    return _MAGIC + bytes([len(a)]) + a + salt + nonce + ct


def open_blob(dek: bytes | bytearray, vault_id: str, blob_id: str, version: int, blob: bytes) -> bytes:
    if not isinstance(blob, (bytes, bytearray)) or blob[:4] != _MAGIC or len(blob) < 6:
        raise Tampered("format")
    n = blob[4]
    alg = bytes(blob[5:5 + n]).decode("ascii", "replace")
    if alg not in ALGS:
        raise UnknownAlgorithm(alg)
    rest = bytes(blob[5 + n:])
    if len(rest) < SALT_BYTES + NONCE_BYTES + 16:
        raise Tampered("format")
    salt, nonce, ct = rest[:SALT_BYTES], rest[SALT_BYTES:SALT_BYTES + NONCE_BYTES], rest[SALT_BYTES + NONCE_BYTES:]
    sub = hkdf(dek, b"pz1/blob" + vault_id.encode("utf-8"), salt=salt)
    try:
        return unpad(AESGCM(sub).decrypt(nonce, ct, blob_aad(vault_id, blob_id, version, alg)))
    except InvalidTag:
        raise Tampered("tag") from None


def check_kdf(params: dict) -> None:
    """Raise :class:`WeakKdf` unless *params* are Argon2id at or above the floor (F5)."""
    if not isinstance(params, dict) or params.get("alg") != "argon2id":
        raise WeakKdf("only Argon2id wraps are written or read")
    for k in ("m", "t", "p"):
        if not isinstance(params.get(k), int):
            raise WeakKdf("bad KDF parameters")
    if params["m"] < KDF_FLOOR["m"] or params["t"] < KDF_FLOOR["t"] or params["p"] < KDF_FLOOR["p"]:
        raise WeakKdf("KDF parameters below the floor")


def kek(secret: str, salt: bytes, params: dict) -> bytes:
    check_kdf(params)
    return Argon2id(salt=salt, length=KEY_BYTES, iterations=params["t"], lanes=params["p"],
                    memory_cost=params["m"]).derive(secret.encode("utf-8"))


def wrap_aad(vault_id: str, way_id: str, kind: str) -> bytes:
    return f"pz1/wrap|{vault_id}|{way_id}|{kind}".encode("utf-8")


def wrap(key: bytes | bytearray, secret: str, aad: bytes, params: dict | None = None) -> dict:
    """Wrap *key* under *secret* with Argon2id + AES-256-GCM. JSON-safe, never holds the secret."""
    params = dict(params or KDF_DEFAULT)
    check_kdf(params)
    salt = os.urandom(16)
    nonce = os.urandom(NONCE_BYTES)
    ct = AESGCM(kek(secret, salt, params)).encrypt(nonce, bytes(key), aad)
    return {"kdf": {**params, "salt": salt.hex()}, "nonce": nonce.hex(), "wrapped": ct.hex()}


def unwrap(record: dict, secret: str, aad: bytes) -> bytearray | None:
    """The wrapped key, or None when *secret* is wrong. Raises on a malformed or weak record."""
    try:
        kdf = dict(record["kdf"])
        salt = bytes.fromhex(kdf.pop("salt"))
        nonce = bytes.fromhex(record["nonce"])
        ct = bytes.fromhex(record["wrapped"])
    except (KeyError, TypeError, ValueError):
        raise Tampered("wrap format") from None
    check_kdf(kdf)
    try:
        return bytearray(AESGCM(kek(secret, salt, kdf)).decrypt(nonce, ct, aad))
    except InvalidTag:
        return None


def raw_wrap(key: bytes | bytearray, by: bytes | bytearray, aad: bytes) -> dict:
    """Wrap *key* under another 32-byte key (the DEK or the machine-unwrapped SDK)."""
    nonce = os.urandom(NONCE_BYTES)
    return {"nonce": nonce.hex(), "wrapped": AESGCM(bytes(by)).encrypt(nonce, bytes(key), aad).hex()}


def raw_unwrap(record: dict, by: bytes | bytearray, aad: bytes) -> bytearray:
    try:
        return bytearray(AESGCM(bytes(by)).decrypt(bytes.fromhex(record["nonce"]),
                                                   bytes.fromhex(record["wrapped"]), aad))
    except (InvalidTag, KeyError, TypeError, ValueError):
        raise Tampered("wrap") from None


def mac(key: bytes | bytearray, info: bytes, data: bytes) -> str:
    return hmac.new(hkdf(key, info), data, hashlib.sha256).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def self_test() -> None:
    """Known-answer and round-trip checks at Core start (F2). Raises CryptoError on failure."""
    # AES-256-GCM known answer (NIST SP 800-38D test case 14: zero key, zero IV, one zero block).
    k = bytes(32)
    iv = bytes(12)
    got = AESGCM(k).encrypt(iv, bytes(16), None).hex()
    if got != "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919":
        raise CryptoError("AES-GCM known answer failed")
    dek = new_key()
    blob = seal(dek, "v", "b", 1, b"canary")
    if open_blob(dek, "v", "b", 1, blob) != b"canary":
        raise CryptoError("round trip failed")
    for vid, bid, ver in (("v", "c", 1), ("v", "b", 2), ("w", "b", 1)):
        try:
            open_blob(dek, vid, bid, ver, blob)
        except Tampered:
            continue
        raise CryptoError("AAD binding failed")
