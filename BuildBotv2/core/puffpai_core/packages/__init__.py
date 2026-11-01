"""The Package Manager (lane L1) and the manifest checks (lane L0).

``packages.manifest`` (L0): ``load``, ``validate``, ``check_files``, ``ManifestError``.

``packages.manager.PackageManager`` (L1), the one install path (01 "Package Manager"):
``add(source, link=False) -> {consent_id, id, card, floor}`` (fetch into
``packages/.staging/<random>``, validate, Drawbridge check, ONE Yes card);
after the owner's Yes, ``install`` runs on its own (rename to ``packages/<id>``
before setup, ``.venv`` + setup, Registry row, Steward starts it, Caretaker
probes it); ``wait(consent_id)``, ``remove(id, keep_data)``, ``update(id)``.
The CLI, the Add page, chat, the wizard and Toolsmith all call these; there is
no second install path. Also: ``packages.fsutil`` (junction-safe copy, hash,
delete), ``packages.installs`` (where each package's folder is),
``packages.mcp_tools`` (a package's tool list from its MCP server),
``packages.check`` (``puffpai check`` Levels 1-3).
"""
