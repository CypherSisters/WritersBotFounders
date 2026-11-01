# Harvested in idea from PuffPAIBuildBot@63c8a037:src/puffpai/integrate/run_here.py (_service_problems :2085, compose_problems :2171) and box/jail.py (proxy names :114-117); rewritten
"""The Docker wall (D30, Z3 section 6.1): a Docker package's only way out is the Bridge proxy.

``override(manifest, compose_path) -> override_path`` writes
``<package>\\.buildbot\\compose.puffpai.yml``. Steward (lane L1) starts the app
with ``docker compose -p pp-<id> -f <theirs> -f <override> up -d``. The app's
own compose file is never edited.

What the override does (each line proven on Docker Desktop 29.6.2 / Compose
v5.3.1, 2026-10-22, see the L4 report; plain Linux Docker in the note below):
1. Every service of the app goes on ``pp-<id>-inner`` only (``networks:
   !override``), a network with ``internal: true``. Its own ports, extra
   hosts, DNS servers and ``network_mode`` are taken away (``!reset``). On an
   internal network a container reaches no internet address, by name or by
   number, and not this computer either (not ``host.docker.internal``, not
   192.168.65.254).
2. One gate, ``pp-gate`` (``alpine/socat`` pinned by digest), joins the inner
   network and ``pp-<id>-out``. It forwards one port, 3128, to the Core's
   proxy on this computer (``host.docker.internal:8421``; Docker Desktop
   reaches a listener bound to 127.0.0.1 this way). It is not a router: an
   app container can't reach the internet through it, only the proxy.
3. Every service gets ``HTTP_PROXY`` / ``HTTPS_PROXY`` (and the npm and Node
   names) pointing at the gate, carrying the package's proxy credential, so
   the proxy knows which package asks (``proxy.Credentials``).
4. Each port the app published becomes one small page gate
   (``pp-page-<n>``) that publishes ``127.0.0.1:<port>`` only and carries
   that one port in to the app's service (Docker publishes nothing from an
   internal-only network).
5. Refused (a :class:`WallRefused`): ``network_mode`` host, ``service:`` or
   ``container:``, ``build.network: host``, a service named like the gate.
   Refused unless the owner gave a floor-level Yes for exactly that
   (``allow_floor=True``, then the package shows "not held"): ``privileged``,
   ``pid: host``, ``ipc: host``, ``userns_mode: host``, ``uts: host``, the
   Docker engine socket, ``devices``, and ``cap_add`` of ``ALL``,
   ``SYS_ADMIN``, ``NET_ADMIN`` or ``NET_RAW``.

"Held" is never taken from this file (F11, D31(e)): :func:`probe` runs a
throwaway container on the inner network and it must fail to reach the
internet and this computer.

Honest notes: traffic a CONNECT proxy can't carry (UDP, NTP, mail, mDNS,
DLNA) does not work behind the wall; image pulls and builds are done by
Docker Desktop itself, outside the wall (install steps, logged under the
package's ``install_hosts``); an app that ignores the proxy variables
reaches nothing (it fails closed).

**Plain Linux Docker** (found at run time by :func:`wall_kw`, never assumed; Docker
Desktop, Windows and Mac get none of this). Three things differ from Docker Desktop:
- ``host.docker.internal`` does not exist, so the gate alone gets
  ``extra_hosts: host.docker.internal:host-gateway``. App services keep
  ``extra_hosts: !reset []``.
- A container can't reach this computer's 127.0.0.1, so the proxy also listens on
  the address of Docker's bridge, at each Docker app's start, which covers an engine
  that came up after the Core (``proxy.py`` says what holds there and what doesn't).
- An ``internal: true`` network still gives this computer an address on it, and an
  app can reach anything listening on every address of this computer through that
  (seen on Docker 26.1.5: a page on 0.0.0.0 answered). So the inner network is made
  with no address on this computer's side (``inhibit_ipv4``).
A firewall such as ufw can stop containers reaching this computer. That is the
owner's firewall and the Core does not change it: :func:`gate_problem` says so in
plain words after the start: Steward's start takes the app down again, a Toolsmith
try is told. While an app runs, a call the firewall drops ends after
``GATE_CONNECT_S``. With the address not found or the bind refused, the Core runs on
and the Docker app does not start.
Not checked on: Docker Desktop for Linux beyond its name, rootless Docker, Podman,
and an engine on another computer (for those the bind or the start refuses).
"""

from __future__ import annotations

import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

GATE_IMAGE = "alpine/socat@sha256:a6be4c0262b339c53ddad723cdd178a1a13271e1137c65e27f90a08c16de02b8"
PROBE_IMAGE = "curlimages/curl@sha256:d9b4541e214bcd85196d6e92e2753ac6d0ea699f0af5741f8c6cccbfcf00ef4b"
GATE = "pp-gate"
GATE_PORT = 3128
HOST_ALIAS = "host.docker.internal"
GATE_CONNECT_S = 5                 # plain Linux: a firewall that drops the gate's call must not hang the app
NO_HOST_ADDRESS = "com.docker.network.bridge.inhibit_ipv4"
_LINUX = sys.platform.startswith("linux")
_SOCK = re.compile(r"(?i)docker\.sock\b|[\\/]{2}\.[\\/]pipe[\\/]docker_engine")
_BAD_CAPS = frozenset({"ALL", "SYS_ADMIN", "NET_ADMIN", "NET_RAW", "CAP_SYS_ADMIN", "CAP_NET_ADMIN",
                       "CAP_NET_RAW", "CAP_ALL"})
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")

Runner = Callable[..., subprocess.CompletedProcess]


class WallRefused(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(" ".join(problems))
        self.problems = problems


def inner_network(package_id: str) -> str:
    return f"pp-{package_id}-inner"


def outer_network(package_id: str) -> str:
    return f"pp-{package_id}-out"


def _run(args: list[str], **kw: Any) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, capture_output=True, text=True, timeout=kw.pop("timeout", 60),
                          creationflags=flags, **kw)


def load_model(compose_path: Path | str, *, env: dict | None = None, runner: Runner = _run) -> dict:
    """The app's compose model with every ``include``, ``extends`` and profile resolved.

    Uses ``docker compose config`` (so nothing the file pulls in is missed).
    Without Docker it reads the YAML itself and refuses a file that uses
    ``include`` or ``extends`` (it could not see every service)."""
    path = Path(compose_path)
    try:
        import os
        r = runner(["docker", "compose", "-f", str(path), "config", "--format", "json"], cwd=str(path.parent),
                   env={**os.environ, **(env or {})})
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            return json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise WallRefused(["The compose file has no services the wall can read."])
    if "include" in data or any(isinstance(s, dict) and "extends" in s for s in data["services"].values()):
        raise WallRefused(["The compose file pulls in other files, and Docker is not running to read them."])
    return data


def _volume_sources(svc: dict) -> list[str]:
    out = []
    for v in svc.get("volumes") or []:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            out.append(str(v.get("source") or ""))
    return out


def check(model: dict) -> tuple[list[str], list[str]]:
    """``(problems, floor_items)``: problems always refuse; floor items need a floor-level Yes."""
    problems: list[str] = []
    floor: list[str] = []
    services = model.get("services") or {}
    for name, svc in services.items():
        if not isinstance(svc, dict):
            problems.append(f"Service {name!r} is not readable.")
            continue
        if name == GATE or str(name).startswith("pp-page-"):
            problems.append(f"Service {name!r} uses a name the Bridge needs for itself.")
        mode = str(svc.get("network_mode") or "")
        if mode and mode not in ("bridge", "default", "none"):
            problems.append(f"{name}: network_mode {mode} would go around the Bridge.")
        build = svc.get("build")
        if isinstance(build, dict) and str(build.get("network") or "") in ("host",):
            problems.append(f"{name}: building with the host network would go around the Bridge.")
        if svc.get("privileged") is True:
            floor.append(f"{name}: privileged")
        for key in ("pid", "ipc", "userns_mode", "uts"):
            if str(svc.get(key) or "") == "host":
                floor.append(f"{name}: {key} host")
        if any(_SOCK.search(s) for s in _volume_sources(svc)):
            floor.append(f"{name}: the Docker engine socket")
        if svc.get("devices"):
            floor.append(f"{name}: devices")
        caps = {str(c).upper() for c in svc.get("cap_add") or []}
        if caps & _BAD_CAPS:
            floor.append(f"{name}: cap_add {', '.join(sorted(caps & _BAD_CAPS))}")
    if not services:
        problems.append("The compose file has no services.")
    return problems, floor


def _published(svc: dict) -> list[tuple[str, int]]:
    """``[(published, target)]`` for a service's ports (long or short form). Published may be a ${VAR}."""
    out = []
    for p in svc.get("ports") or []:
        if isinstance(p, dict):
            target = p.get("target")
            published = p.get("published")
            if target and published not in (None, ""):
                out.append((str(published), int(target)))
        elif isinstance(p, (str, int)):
            parts = str(p).split("/")[0]
            bits = re.split(r":(?![^{]*})", parts)
            if len(bits) >= 2:
                try:
                    out.append((bits[-2], int(bits[-1].split("-")[0])))
                except ValueError:
                    continue
    return out


def _q(v: Any) -> str:
    return json.dumps(v)


def render(package_id: str, model: dict, proxy_url: str, *, host_proxy_port: int = 8421,
           host_gateway: bool = False) -> str:
    """The override text. Pure: same input, same text. *host_gateway* is plain Linux Docker (:func:`wall_kw`)."""
    if not _NAME.match(package_id):
        raise WallRefused([f"{package_id!r} is not a package id."])
    services = model.get("services") or {}
    inner, outer = inner_network(package_id), outer_network(package_id)
    m = re.match(r"^http://([^:@/]+):([^@/]+)@[^/]+$", proxy_url or "")
    if not m:
        raise WallRefused(["The proxy credential is missing."])
    gate_url = f"http://{m.group(1)}:{m.group(2)}@{GATE}:{GATE_PORT}"
    no_proxy = ",".join(["localhost", "127.0.0.1", "::1", *sorted(services)])
    env = {k: gate_url for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                                 "npm_config_proxy", "npm_config_https_proxy")}
    env.update(NODE_USE_ENV_PROXY="1", NO_PROXY=no_proxy, no_proxy=no_proxy)
    lines = ["# Written by the PuffPAI Core (the Docker wall, D30). Do not edit: it is rewritten at every start.",
             "services:"]
    pages: list[tuple[str, str, int]] = []
    for name in sorted(services):
        svc = services[name] if isinstance(services[name], dict) else {}
        lines += [f"  {name}:",
                  "    networks: !override", f"      {inner}: {{}}",
                  "    ports: !reset []", "    extra_hosts: !reset []", "    dns: !reset []"]
        if svc.get("network_mode") not in (None, "", "none"):
            lines.append("    network_mode: !reset null")
        lines.append("    environment:")
        lines += [f"      {k}: {_q(v)}" for k, v in env.items()]
        for published, target in _published(svc):
            pages.append((name, published, target))
    dial = f"tcp-connect:{HOST_ALIAS}:{host_proxy_port}" + (f",connect-timeout={GATE_CONNECT_S}" if host_gateway else "")
    lines += [f"  {GATE}:", f"    image: {_q(GATE_IMAGE)}",
              f"    command: {_q([f'tcp-listen:{GATE_PORT},fork,reuseaddr', dial])}",
              "    networks:", f"      {inner}: {{}}", f"      {outer}: {{}}", "    read_only: true",
              "    cap_drop: [\"ALL\"]", "    security_opt: [\"no-new-privileges:true\"]"]
    if host_gateway:
        lines.append(f"    extra_hosts: [{_q(f'{HOST_ALIAS}:host-gateway')}]")     # the gate only, never an app
    for i, (svc_name, published, target) in enumerate(pages, 1):
        lines += [f"  pp-page-{i}:", f"    image: {_q(GATE_IMAGE)}",
                  f"    command: {_q([f'tcp-listen:{target},fork,reuseaddr', f'tcp-connect:{svc_name}:{target}'])}",
                  "    networks:", f"      {inner}: {{}}", f"      {outer}: {{}}",
                  f"    ports: [{_q(f'127.0.0.1:{published}:{target}')}]", "    read_only: true",
                  "    cap_drop: [\"ALL\"]", "    security_opt: [\"no-new-privileges:true\"]",
                  f"    depends_on: [{_q(svc_name)}]"]
    lines += ["networks:", f"  {inner}:", f"    name: {inner}", "    internal: true"]
    if host_gateway:
        lines += ["    driver_opts:", f"      {NO_HOST_ADDRESS}: \"true\""]
    lines += [f"  {outer}:", f"    name: {outer}"]
    for net in sorted((model.get("networks") or {})):
        if net not in (inner, outer):
            lines.append(f"  {net}: !reset {{}}")
    return "\n".join(lines) + "\n"


def override(manifest: dict, compose_path: Path | str, *, proxy_url: str | None = None,
             allow_floor: bool = False, package_dir: Path | str | None = None, model: dict | None = None,
             env: dict | None = None, host_proxy_port: int = 8421, host_gateway: bool = False,
             runner: Runner = _run) -> Path:
    """Write the wall for one Docker package; returns the override's path (lane L1 calls this at each start).

    *proxy_url* is the package's fresh proxy credential (``proxy.Credentials.issue``);
    when None, the running Core's credentials mint one. ``allow_floor`` only after the
    owner's floor-level Yes for exactly those items; the package then shows "not held".
    """
    package_id = str(manifest.get("id") or "")
    model = model if model is not None else load_model(compose_path, env=env, runner=runner)
    problems, floor = check(model)
    if problems:
        raise WallRefused(problems)
    if floor and not allow_floor:
        raise WallRefused([f"{item} can get around the Bridge, so it needs your PIN or Windows Hello first."
                           for item in floor])
    if proxy_url is None:
        from .service import current
        proxy_url = current().credentials.issue(package_id)
    text = render(package_id, model, proxy_url, host_proxy_port=host_proxy_port, host_gateway=host_gateway)
    folder = Path(package_dir) if package_dir else Path(compose_path).parent
    out = folder / ".buildbot" / "compose.puffpai.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    (out.parent / "wall.json").write_text(json.dumps({"package": package_id, "not_held": sorted(floor),
                                                      "held_by_probe": None}, indent=1), encoding="utf-8")
    return out


def _gateways(network: str, runner: Runner) -> list[str]:
    """The IPv4 gateway addresses Docker names for *network*. Empty when the engine gives no readable answer."""
    r = runner(["docker", "network", "inspect", network, "--format", "{{json .IPAM.Config}}"], timeout=20)
    if r.returncode != 0:
        return []
    try:
        config = json.loads(r.stdout or "null") or []
        found = [str(c.get("Gateway") or "") for c in config if isinstance(c, dict)]
    except (ValueError, AttributeError):
        return []
    return [g for g in found if g and "." in g]


def bridge_gateway(*, runner: Runner = _run) -> str:
    """The address of Docker's bridge on this computer, or ``""``. Only a private address is an answer:
    never every address, this computer's loopback, a link-local or a public address."""
    try:
        found = _gateways("bridge", runner)
    except (OSError, subprocess.SubprocessError):
        return ""
    for gateway in found:
        try:
            ip = ipaddress.ip_address(gateway)
        except ValueError:
            continue
        if ip.is_private and not (ip.is_unspecified or ip.is_loopback or ip.is_link_local):
            return gateway
    return ""


def _engine_os(runner: Runner) -> str:
    """What the engine calls its operating system. Raises :class:`WallRefused` in plain words when none answers."""
    why = "Docker is installed, but its engine is not answering. Start Docker, then start this app again."
    try:
        r = runner(["docker", "info", "--format", "{{.OperatingSystem}}"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        raise WallRefused([why]) from None
    if r.returncode == 0 and (r.stdout or "").strip():
        return r.stdout.strip()
    if "permission denied" in f"{r.stdout or ''} {r.stderr or ''}".lower():
        why = ("Docker is installed, but this user may not use it. Add this user to the docker group, sign in again, "
               "then start this app again.")
    raise WallRefused([why])


def wall_kw(proxy: Any, *, runner: Runner = _run) -> dict:
    """The wall's arguments for this start, read at this start: ``override(..., **wall_kw(proxy))``.

    On plain Linux Docker this also makes the proxy listen on Docker's bridge, so it is there in time for the
    compose start, whenever the engine came up. Anywhere else nothing changes, and with no ``docker`` program
    the start goes on to say "Docker is not installed on this computer." Raises :class:`WallRefused` in plain
    words when a Docker app can't have a way out here; only that app is refused, the Core runs on."""
    kw: dict = {"host_proxy_port": int(proxy.port)}
    if not _LINUX or not shutil.which("docker"):
        return kw
    if "docker desktop" in _engine_os(runner).lower():
        return kw                          # Docker Desktop has host.docker.internal and reaches 127.0.0.1 itself
    address = bridge_gateway(runner=runner)
    if not address:
        raise WallRefused(["The address of Docker's bridge network could not be found, so this Docker app would "
                           "have no way to the Bridge. It was not started."])
    try:
        proxy.listen_also(address)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        why = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        raise WallRefused([f"The Bridge proxy could not listen on Docker's bridge address {address}, port {kw['host_proxy_port']} "
                           f"({why}), so this Docker app would have no way to the Bridge. It was not started."]) from None
    return {**kw, "host_gateway": True}


def gate_problem(run: Callable[[list[str]], int], host_proxy_port: int, *, tries: int = 2, pause: float = 1.0) -> str:
    """After a start on plain Linux Docker: can the app's gate reach the proxy? ``""``, or the plain reason.
    *run* runs ``docker compose <args>`` for this app and gives the exit code. A bare connection: it opens
    nothing and sends no credential, so the proxy writes no ledger line for it."""
    args = ["exec", "-T", GATE, "socat", "-u", "OPEN:/dev/null",
            f"TCP:{HOST_ALIAS}:{int(host_proxy_port)},connect-timeout={GATE_CONNECT_S}"]
    for attempt in range(max(1, tries)):
        if attempt:
            time.sleep(pause)
        if run(args) == 0:
            return ""
    return (f"This Docker app can't reach the Bridge proxy on this computer (port {int(host_proxy_port)}). A firewall "
            "such as ufw may be stopping containers from reaching this computer. PuffPAI does not change your "
            "firewall. Allow Docker's networks to reach that port, then start the app again.")


def _is_here(address: str) -> bool:
    """Is *address* one of this computer's own addresses? Only binding a throwaway socket to it, no listener."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((address, 0))
        return True
    except OSError:
        return False


def probe(package_id: str, *, core_port: int = 8420, proxy_port: int | None = None, runner: Runner = _run) -> dict:
    """The Core's own test (F11): from the inner network, try the internet and this computer.
    Every try must fail. ``{"held": bool, "reached": [what got through]}``.

    On Linux this computer has other addresses than Docker Desktop's. A closed port looks the same as no way
    there, so the inner network's gateway must also not be an address of this computer at all."""
    net = inner_network(package_id)
    tries = {"internet_by_number": "https://1.1.1.1", "internet_by_name": "https://example.com",
             "this_computer_by_name": f"http://{HOST_ALIAS}:{core_port}",
             "this_computer_by_number": f"http://192.168.65.254:{core_port}"}
    reached = []
    if _LINUX:
        try:
            inner = _gateways(net, runner)
        except (OSError, subprocess.SubprocessError):
            inner = []
        if not inner:
            return {"held": False, "reached": ["probe_failed"]}
        if any(_is_here(g) for g in inner):
            reached.append("this_computer_by_inner_gateway")
        tries.update({f"this_computer_at_{g}": f"http://{g}:{core_port}" for g in inner})
        bridge = bridge_gateway(runner=runner)
        if bridge:
            tries[f"this_computer_at_{bridge}"] = f"http://{bridge}:{core_port}"
            if proxy_port:
                tries["proxy_without_the_gate"] = f"http://{bridge}:{proxy_port}"
    for what, url in tries.items():
        try:
            r = runner(["docker", "run", "--rm", "--network", net, PROBE_IMAGE, "-s", "-k", "-m", "5",
                        "-o", "/dev/null", url], timeout=60)
        except (OSError, subprocess.SubprocessError):
            return {"held": False, "reached": ["probe_failed"]}
        if r.returncode == 0:
            reached.append(what)
        elif r.returncode == 125:          # docker itself could not run the probe: not proven
            return {"held": False, "reached": ["probe_failed"]}
    return {"held": not reached, "reached": reached}


__all__ = ["GATE", "GATE_IMAGE", "PROBE_IMAGE", "WallRefused", "bridge_gateway", "check", "gate_problem", "inner_network",
           "load_model", "outer_network", "override", "probe", "render", "wall_kw"]
