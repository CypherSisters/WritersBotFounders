"""Integration lane I2b: the Docker wall LIVE (D30), end to end through the real Core.

A tiny compose app (two images already pinned by the wall itself: ``alpine/socat`` serves one page,
``curlimages/curl`` asks the internet) is installed as a ``docker_compose`` package through the one
install path, with ``needs.hosts: [example.com]`` and the owner's Yes. Then:
- it runs behind the wall (its services are on the internal network only);
- its page loads through the Core's pages listener (``walltest.localhost:<pages port>``);
- it reaches its declared host through the Bridge proxy, and an undeclared host is refused;
- a direct connection (no proxy) reaches nothing: neither a host name nor a bare IP address (1.1.1.1 on
  80 and 443), so it is the wall and not only a missing DNS that stops it.
Everything it made is removed at the end (``docker compose down``, then its two networks by name).
Marked ``docker``: skipped when Docker is not running.
"""

import ipaddress
import json
import shutil
import socket
import subprocess
import threading
import urllib.request

import pytest

from conftest import wait_for, yes
from puffpai_core.zone3 import docker_wall as dw

ID = "walltest"
PROJECT = f"puffpai-{ID}"


def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = [pytest.mark.docker, pytest.mark.skipif(not _docker_up(), reason="Docker is not running")]

COMPOSE = f"""services:
  web:
    image: {dw.GATE_IMAGE}
    command: ["TCP-LISTEN:8080,fork,reuseaddr", "SYSTEM:echo HTTP/1.0 200 OK; echo; echo hello from behind the wall"]
    ports: ["${{PUFFPAI_PORT}}:8080"]
  asker:
    image: {dw.PROBE_IMAGE}
    entrypoint: ["sleep", "600"]
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _package(tmp_path):
    pkg = tmp_path / "src" / ID
    pkg.mkdir(parents=True)
    (pkg / "compose.yaml").write_text(COMPOSE, encoding="utf-8")
    (pkg / "README.md").write_text("# Wall test\n\n## PuffPAI\n\nOne page behind the Docker wall.\n", encoding="utf-8")
    (pkg / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": ID, "kind": "app", "name": "Wall Test", "version": "1.0.0",
        "description": "One page, served behind the Docker wall.", "author": "Cypher Sisters", "license": "MIT",
        "run": {"windows": {"type": "docker_compose", "file": "compose.yaml"},
                "default": {"type": "docker_compose", "file": "compose.yaml"}},
        "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 2},
        "ui": {"pages": [{"id": "main", "title": "Wall Test", "path": "/", "show": "in_desktop"}]},
        "needs": {"hosts": ["example.com"]},
        "consent": "Wall Test serves one page and may reach example.com."}), encoding="utf-8")
    return pkg


def _compose(*args, timeout=120):
    return subprocess.run(["docker", "compose", "-p", PROJECT, *args], capture_output=True, text=True,
                          timeout=timeout)


@pytest.fixture
def live(state, tmp_path):
    from puffpai_core.api.app import create_app, make_servers, start_core, stop_core, stop_servers
    port, pages = _free_port(), _free_port()
    app = create_app(state, port=port, pages_port=pages)
    core = app.extensions["puffpai"]
    core.proxy_port = 0
    terminal = start_core(app)
    servers = make_servers(app, port, pages)
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()
    try:
        yield core, pages, _package(tmp_path)
    finally:
        stop_servers(servers)
        stop_core(core, terminal)                                   # Steward runs docker compose down
        _compose("down", "-v", "--remove-orphans", "-t", "1")       # and again, in case a start half-failed
        for net in (dw.inner_network(ID), dw.outer_network(ID)):
            subprocess.run(["docker", "network", "rm", net], capture_output=True, timeout=60)


def _ask(service: str, url: str, *proxy_args: str) -> subprocess.CompletedProcess:
    return _compose("exec", "-T", service, "curl", "-s", "-m", "15", "-o", "/dev/null", "-w", "%{http_code}",
                    *proxy_args, url, timeout=90)


def test_a_compose_app_runs_behind_the_wall_end_to_end(live, drawbridge):
    core, pages, pkg = live
    staged = core.packages.add(str(pkg))
    assert "example.com" in staged["card"]["hosts"]
    assert yes(core, staged)["status"] == "installed"
    core.zone3.bridge.open(by="owner")
    row = wait_for(lambda: (r := core.registry.get(ID)) and r["status"] == "running" and r, 180, 1)
    assert row, core.registry.get(ID)

    # 1. behind the wall: the app's services are on the internal network only
    inspect = subprocess.run(["docker", "network", "inspect", dw.inner_network(ID)], capture_output=True,
                             text=True, timeout=60)
    assert inspect.returncode == 0 and json.loads(inspect.stdout)[0]["Internal"] is True
    ps = [json.loads(x) for x in _compose("ps", "--format", "json").stdout.splitlines() if x.strip().startswith("{")]
    assert {p.get("Service") for p in ps} >= {"web", "asker", dw.GATE}
    for name in ("web", "asker"):
        nets = subprocess.run(["docker", "inspect", "-f", "{{json .NetworkSettings.Networks}}",
                               f"{PROJECT}-{name}-1"], capture_output=True, text=True, timeout=60)
        assert list(json.loads(nets.stdout)) == [dw.inner_network(ID)], nets.stdout

    # 2. its page loads through the Core's pages listener, on its own origin (D53)
    req = urllib.request.Request(f"http://127.0.0.1:{pages}/", headers={"Host": f"{ID}.localhost:{pages}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    body = wait_for(lambda: _get(opener, req), 60, 1)
    assert body and "hello from behind the wall" in body

    # 3. the declared host through the Bridge proxy; an undeclared host is refused
    granted = _ask("asker", "https://example.com/")
    refused = _ask("asker", "https://example.org/")
    direct = _ask("asker", "https://example.com/", "--noproxy", "*")
    # A name that can't resolve proves only DNS is gone: also go straight to an IP address, no proxy, no DNS.
    direct_ip = _ask("asker", "http://1.1.1.1:80/", "--noproxy", "*")
    direct_ip_tls = _ask("asker", "https://1.1.1.1:443/", "--noproxy", "*", "-k")
    lines = [x for x in core.zone3.bridge.ledger.read(limit=1000) if x.get("subject") == f"pkg:{ID}"]
    print("granted:", granted.returncode, granted.stdout, "refused:", refused.returncode, refused.stdout,
          "direct:", direct.returncode, direct.stdout, "direct ip:", direct_ip.returncode, direct_ip.stdout,
          direct_ip_tls.returncode, direct_ip_tls.stdout)
    print("ledger:", [(x["host"], x["outcome"], x["code"]) for x in lines])
    assert granted.returncode == 0 and granted.stdout.strip() == "200"
    assert refused.returncode != 0
    assert direct.returncode != 0                                   # no way out but the proxy
    for d in (direct_ip, direct_ip_tls):                            # not even to a bare IP address
        assert d.returncode != 0 and d.stdout.strip() in ("", "000"), (d.returncode, d.stdout, d.stderr)
    assert not any(x["host"] == "1.1.1.1" and x["outcome"] == "done" for x in lines)
    assert any(x["host"] == "example.com" and x["outcome"] == "done" for x in lines)
    assert any(x["host"] == "example.org" and x["outcome"] == "refused" for x in lines)
    if dw._LINUX:
        _plain_linux_checks(core)


def _plain_linux_checks(core):
    """Plain Linux Docker only (D132). Docker Desktop's numbers are not this computer's, so they prove nothing there."""
    proxy_port = core.zone3.proxy.port
    bridge = dw.bridge_gateway()
    assert bridge and bridge in core.zone3.proxy._also                   # it listens there, and on 127.0.0.1
    # 1. only the gate has the name for this computer
    has_name = _compose("exec", "-T", "asker", "grep", "-c", dw.HOST_ALIAS, "/etc/hosts")
    assert has_name.stdout.strip() == "0" and has_name.returncode != 0, has_name.stdout
    assert dw.HOST_ALIAS in _compose("exec", "-T", dw.GATE, "cat", "/etc/hosts").stdout
    # 2. a page on every address of this computer is not reachable from an app, by either of its addresses
    inner = dw._gateways(dw.inner_network(ID), dw._run)                  # Docker 29 names none; 26 names one not here
    assert inner is not None and not any(dw._is_here(g) for g in inner)
    # Not taken from what the engine names: no address of this computer lies in the inner network at all.
    from puffpai_core.api.network import _interface_table
    subnets = json.loads(subprocess.run(["docker", "network", "inspect", dw.inner_network(ID), "--format",
                                         "{{json .IPAM.Config}}"], capture_output=True, text=True, timeout=30).stdout)
    nets = [ipaddress.ip_network(c["Subnet"]) for c in subnets]
    mine = [a for _, a in _interface_table()]
    assert nets and mine and not any(ipaddress.ip_address(a) in n for a in mine for n in nets if n.version == 4), (mine, nets)
    canary = socket.socket()
    canary.bind(("0.0.0.0", 0))
    canary.listen(5)
    port = canary.getsockname()[1]
    try:
        for address in (*inner, bridge):
            got = _ask("asker", f"http://{address}:{port}/", "--noproxy", "*", "-m", "6")
            print("canary", address, got.returncode, got.stdout)
            assert got.returncode != 0 and got.stdout.strip() in ("", "000")
    finally:
        canary.close()
    # 3. nor can an app reach the proxy's second listener without its gate
    direct = _ask("asker", f"http://{bridge}:{proxy_port}/", "--noproxy", "*", "-m", "6")
    assert direct.returncode != 0 and direct.stdout.strip() in ("", "000"), direct.stdout
    # 4. a container on Docker's ordinary bridge can reach that listener, and with no credential it gets 407
    stranger = subprocess.run(["docker", "run", "--rm", dw.PROBE_IMAGE, "-s", "-m", "10", "-o", "/dev/null", "-w",
                               "%{http_code}", "-x", f"http://{bridge}:{proxy_port}", "http://example.com/"],
                              capture_output=True, text=True, timeout=120)
    print("stranger:", stranger.returncode, stranger.stdout)
    assert stranger.stdout.strip() == "407"
    lines = core.zone3.bridge.ledger.read(limit=1000)
    assert any(x["code"] == "bad_credential" and x["outcome"] == "refused" for x in lines)
    # 5. the Core's own test agrees: the wall held
    held = dw.probe(ID, core_port=port, proxy_port=proxy_port)
    print("probe:", held)
    assert held == {"held": True, "reached": []}


def _get(opener, req):
    try:
        with opener.open(req, timeout=10) as r:
            return r.read().decode("utf-8", "replace")
    except OSError:
        return None
