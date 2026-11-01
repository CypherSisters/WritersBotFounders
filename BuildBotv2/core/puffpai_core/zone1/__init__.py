"""Zone 1, the Vault (lane L3).

Built (L3), all Core code; providers only ever see ciphertext:
- ``vault.Vault``: the Core key scheme (DEK, per-write HKDF subkeys, AES-256-GCM
  with AAD bound to vault, blob id and version; one item per blob; a root with an
  epoch for rollback), PIN and recovery code (two ways in at least), throttle,
  idle lock, the unattended tier (the machine wrap, her tick per key), tombstones for
  delete while locked. ``vault.*`` events are published by the Core only.
- ``store_local.LocalStore``: the default ``zone1.vault-store`` (files, atomic put).
- ``archivist.Archivist``: the only key holder. ``lend`` / ``use`` / ``end*``
  (the session lease, 3 hours at most), ``verify_pin`` (floor-level Yes, D29),
  ``hold`` / ``resolve`` / ``expire``, ``inject``; ``archivist.for_state`` gives
  the one Archivist per bot folder.
- ``inbox.Inbox``: the sealed inbox (D36(a)): ``seal(name, value)``, filed at setup or unlock.
- ``classify.classify(text | file=...)``: labels with spans, whole text, floor always on.
- ``gate.prepare(text, vault_open) -> (text_for_model, handles)``: the gate before Zone 2.
- ``folders.Library``: data folder and linked folders, private finds held, FTS5 index.
- ``secret_detector`` (floor key detector, from L0), ``detectors`` (personal data),
  ``labels`` (the floor and default packs), ``pin_rules``, ``crypto``, ``machine_key``.

Not built yet: store moves, backups, community unlock factors, process detectors,
installed label packs.
"""
