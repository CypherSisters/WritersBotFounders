"""``puffpai <command>`` for the real-console tests (``console_kit``), with the test suite's own guards.

The Core runs in a process of its own here, so conftest's fixtures don't reach it. The same guards are set
before the CLI starts: no keyring, no real DPAPI (zone1's machine key and Zone 3's anchors), and a fake
machine wrap for the anchors (conftest ``FakeMachine``).
"""

import os
import sys

os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"

import no_real_browser  # noqa: E402
from puffpai_core import anchor, machine_file, window  # noqa: E402

no_real_browser.install()
from puffpai_core.packages import store_index  # noqa: E402
from puffpai_core.zone1 import machine_key  # noqa: E402


class FakeMachine:
    def available(self) -> bool:
        return True

    def protect(self, data: bytes) -> bytes:
        return b"fake-wrap:" + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"fake-wrap:"):
            raise ValueError("not a fake wrap")
        return bytes(b ^ 0x5A for b in data[len(b"fake-wrap:"):])


def _refuse(*_a, **_k):
    raise AssertionError("a test called real DPAPI")


def _no_network(url, **_k):
    raise store_index.Unreachable("it could not be reached (no network in a test)")


def _no_window(port, state=None):
    return False, window.cant_line("a test never opens a real window", port)


def _no_shortcuts(*_a, **_k):
    return False, "A test never makes real shortcuts."


window.open_window = _no_window             # the default way in (D81): a console test goes on to the chat instead
window.make_shortcuts = _no_shortcuts       # never on her real desktop or Start menu
store_index.http_get = _no_network          # the Desktop's store panel asks for the index (D96): never for real here
anchor._dpapi = _refuse
anchor.default_machine = FakeMachine
machine_key.protect = _refuse
machine_key.unprotect = _refuse
machine_file._wrap_key = _refuse


def _store_plan_hook() -> None:
    """``PUFFPAI_TEST_STORE_PLAN=1`` (lane I16): once the Core has started, one plan whose only job has the
    PuffPAI store's FreshTable as its offer, made through the host's own ``agent_plan`` (the route Toolsmith's
    process calls), so its Yes card, the ticks and the store adds are the real Core's. Only the Toolsmith
    process that would have planned it is left out (it needs a brain)."""
    from puffpai_core.api import app as app_mod
    real = app_mod.start_core

    def start_core(app):
        got = real(app)
        host = app_mod.start_toolsmith(app)
        job = {"name": "Shopping list", "does": "keep a shopping list", "sample_ask": "Put milk on my shopping list"}
        b = host.store.create("keep a shopping list", [job], host.settings())
        shop = {"package": "freshtable", "name": "FreshTable", "tool": "freshtable.add_shopping",
                "picked": [{"ask": job["sample_ask"], "tool": "freshtable.add_shopping"}]}
        host.agent_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": [], "store": shop}}]})
        return got
    app_mod.start_core = start_core


if os.environ.get("PUFFPAI_TEST_STORE_PLAN") == "1":
    _store_plan_hook()

def main() -> int:
    """``puffpai.exe`` for a test's own ``Start BuildBot.cmd`` (lane I6b): the launcher runs ``serve`` with no
    ports, so the test's ports come from ``PUFFPAI_TEST_PORTS`` (core,pages,proxy); without them ``serve``
    is refused, so a test never binds 8420-8422. ``PUFFPAI_TEST_EXIT``: ``serve`` ends at once with that code (a
    BuildBot that stopped by itself; a code below zero is a crash code such as 0xC0000005).
    ``PUFFPAI_TEST_PAIR_EXIT``: only ``pair`` ends at once with that code. ``PUFFPAI_TEST_START_WAIT``: how long
    ``start`` (D94) waits for the Core it started, in seconds."""
    from puffpai_core.cli import main as cli_main
    argv = sys.argv[1:]
    if os.environ.get("PUFFPAI_TEST_PAIR_EXIT") and argv[:1] == ["pair"]:
        print("pair failed (a test).", file=sys.stderr, flush=True)
        return int(os.environ["PUFFPAI_TEST_PAIR_EXIT"])
    if os.environ.get("PUFFPAI_TEST_EXIT") and argv[:1] == ["serve"]:
        print("BuildBot stopped by itself (a test).", file=sys.stderr, flush=True)
        return int(os.environ["PUFFPAI_TEST_EXIT"])
    if os.environ.get("PUFFPAI_TEST_START_WAIT") and argv[:1] == ["start"]:
        argv += ["--wait", os.environ["PUFFPAI_TEST_START_WAIT"]]
    if argv[:1] == ["serve"] and "--port" not in argv:
        ports = os.environ.get("PUFFPAI_TEST_PORTS", "").split(",")
        if len(ports) != 3 or not all(p.isdigit() for p in ports):
            raise SystemExit("A test's serve needs PUFFPAI_TEST_PORTS, so it never uses 8420-8422.")
        argv += ["--port", ports[0], "--pages-port", ports[1], "--proxy-port", ports[2]]
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
