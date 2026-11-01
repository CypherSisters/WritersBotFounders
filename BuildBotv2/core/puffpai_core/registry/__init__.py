"""The Registry (lane L0): the single list of everything installed.

Interface: ``Registry(path=None)`` with ``add``, ``update``, ``set_health``,
``get``, ``list``, ``remove``, ``validate(live_tool_names)``; and
``row_from_manifest(manifest, origin, **extra)``. Rows follow
``contracts/registry.schema.json``.
"""

from .store import NOT_CHECKED, Registry, RegistryError, row_from_manifest

__all__ = ["NOT_CHECKED", "Registry", "RegistryError", "row_from_manifest"]
