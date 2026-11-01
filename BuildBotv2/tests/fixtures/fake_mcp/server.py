"""A fake package MCP server for lane L2's tests (stdio or HTTP), like FreshTable's list.

Run as ``server.py <list file> <expected token>``. It refuses to start unless
PUFFPAI_TOKEN (the Core passes it) is the expected token. The list is kept in
the list file, so the test can read what was added.
"""

import os
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

srv = MCPServer("fake-shop")
LIST = Path(sys.argv[1] if len(sys.argv) > 1 else "fake_list.txt")


@srv.tool()
def add_to_list(item: str) -> str:
    """Put an item on the shopping list."""
    with LIST.open("a", encoding="utf-8") as fh:
        fh.write(item + "\n")
    return f"Added {item} to the shopping list."


@srv.tool()
def show_list() -> str:
    """Show what is on the shopping list."""
    return LIST.read_text(encoding="utf-8") if LIST.exists() else "The list is empty."


if __name__ == "__main__":
    if len(sys.argv) < 3 or os.environ.get("PUFFPAI_TOKEN") != sys.argv[2]:
        sys.exit("refused: no PUFFPAI_TOKEN")
    srv.run("stdio")
