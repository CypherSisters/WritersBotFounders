"""``python -I -m puffpai_core.zone3.toolsmith``: Toolsmith's own process (09 section 3, point 1).

The Core starts it and writes ONE JSON line to its stdin:
``{"token", "api", "do": "plan" | "build", "build", "cache"}``. The token
opens only the Toolsmith routes for that build. It is kept in memory only:
never printed, never put in a file or an environment variable, and never
handed to a program (Toolsmith starts none; the Core runs every command).
"""

from __future__ import annotations

import json
import sys

from . import agent
from .link import Link, http_transport


def main() -> int:
    line = sys.stdin.readline()
    try:
        msg = json.loads(line)
    except ValueError:
        print("Toolsmith got no start message from the Core.", file=sys.stderr)
        return 2
    token = str(msg.pop("token", "") or "")
    if not token or not msg.get("build"):
        print("Toolsmith got no token or build from the Core.", file=sys.stderr)
        return 2
    link = Link(http_transport(str(msg.get("api") or ""), token), str(msg["build"]))
    token = ""
    try:
        return agent.run(msg, link)
    except Exception as exc:  # noqa: BLE001 - the Core holds what was left; say what kind, never the token
        print(f"Toolsmith stopped: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
