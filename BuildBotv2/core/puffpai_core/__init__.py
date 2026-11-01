"""PuffPAI Core: the three zones, the seven agents, the Registry, the Bus,
the Package Manager, the Checklist and the Core API.

The one rule: the Core never imports, edits or special-cases package code.
Packages talk to the Core only through the Core API, and to the brain only
through MCP. The contracts in ``contracts/`` at the repo root come first;
this code follows them.
"""

__version__ = "0.1.0"
