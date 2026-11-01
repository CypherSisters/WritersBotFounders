"""The Docker wall (D30). Unit tests on the override, and live tests marked ``docker``
(skipped when Docker is not running). Live tests use two tiny public images pinned by digest,
a project named ``pp-l4walltest``, and remove every container and network they make."""

import os
import shutil
import socket
import subprocess

import pytest

from puffpai_core.zone3 import docker_wall as dw
from z3helpers import grant, manifest, zone

URL = "http://l4walltest:s3cr3t-for-tests@127.0.0.1:8421"


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


def _model(**svc):
    base = {"image": "example/app", "networks": ["default", "outside"], "ports": ["0.0.0.0:8080:80"],
            "extra_hosts": ["evil:1.2.3.4"], "dns": ["8.8.8.8"]}
    base.update(svc)
    return {"services": {"app": base, "db": {"image": "example/db"}}, "networks": {"outside": {}}}


def test_render_puts_every_service_behind_the_gate():
    text = dw.render("l4walltest", _model(), URL)
    assert text.count("networks: !override") == 2 and "ports: !reset []" in text
    assert "internal: true" in text and dw.GATE_IMAGE in text
    assert "tcp-connect:host.docker.internal:8421" in text
    assert '"127.0.0.1:8080:80"' in text and "0.0.0.0" not in text
    assert "@pp-gate:3128" in text and "127.0.0.1:8421" not in text.split("tcp-connect")[0]
    assert "outside: !reset {}" in text


@pytest.mark.parametrize("svc, word", [({"network_mode": "host"}, "network_mode"),
                                       ({"network_mode": "container:other"}, "network_mode"),
                                       ({"build": {"context": ".", "network": "host"}}, "host network")])
def test_ways_around_the_wall_are_refused(tmp_path, svc, word):
    with pytest.raises(dw.WallRefused) as e:
        dw.override(manifest("l4walltest"), tmp_path / "compose.yaml", proxy_url=URL, model=_model(**svc),
                    allow_floor=True)
    assert word in str(e.value)


@pytest.mark.parametrize("svc", [{"privileged": True}, {"pid": "host"},
                                 {"volumes": ["//./pipe/docker_engine://./pipe/docker_engine"]},
                                 {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]},
                                 {"cap_add": ["NET_ADMIN"]}, {"devices": ["/dev/net/tun"]}])
def test_floor_items_need_a_floor_level_yes(tmp_path, svc):
    with pytest.raises(dw.WallRefused) as e:
        dw.override(manifest("l4walltest"), tmp_path / "compose.yaml", proxy_url=URL, model=_model(**svc))
    assert "PIN or Windows Hello" in str(e.value)
    out = dw.override(manifest("l4walltest"), tmp_path / "compose.yaml", proxy_url=URL, model=_model(**svc),
                      allow_floor=True)
    assert out.name == "compose.puffpai.yml"
    import json
    assert json.loads((out.parent / "wall.json").read_text())["not_held"], "it shows not held"


def test_without_docker_an_include_is_refused(tmp_path):
    (tmp_path / "compose.yaml").write_text("include: [other.yaml]\nservices:\n  app: {image: x}\n", encoding="utf-8")
    fail = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no docker")   # noqa: E731
    with pytest.raises(dw.WallRefused):
        dw.load_model(tmp_path / "compose.yaml", runner=fail)


def test_a_missing_credential_is_refused():
    with pytest.raises(dw.WallRefused):
        dw.render("l4walltest", _model(), "")


# -- live ---------------------------------------------------

def _docker_up() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True,
                              timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


docker = pytest.mark.skipif(not _docker_up(), reason="Docker is not running")
PROJECT = "pp-l4walltest"


def _dc(*args, cwd, env=None, timeout=180):
    return subprocess.run(["docker", "compose", "-p", PROJECT, *args], cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, **(env or {})})


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def walled(state, tmp_path):
    pkg = tmp_path / "packages" / "l4walltest"
    pkg.mkdir(parents=True)
    (pkg / "compose.yaml").write_text(
        "services:\n"
        "  app:\n"
        f"    image: {dw.PROBE_IMAGE}\n"
        "    entrypoint: [\"sleep\", \"120\"]\n"
        "    ports: [\"0.0.0.0:${PUFFPAI_PORT}:8080\"]\n"
        "    networks: [default, outside]\n"
        "networks:\n"
        "  outside: {}\n", encoding="utf-8")
    z = zone(state)
    z.start_proxy(port=0)
    env = {"PUFFPAI_PORT": str(_free_port())}
    url = z.package_env("l4walltest")["HTTPS_PROXY"]
    out = dw.override(manifest("l4walltest"), pkg / "compose.yaml", proxy_url=url, env=env, **dw.wall_kw(z.proxy))
    yield z, pkg, out, env
    _dc("-f", "compose.yaml", "-f", str(out), "down", "-v", "--remove-orphans", "-t", "1", cwd=pkg, env=env)
    z.stop()


@docker
@pytest.mark.docker
def test_compose_merges_the_override(walled):
    z, pkg, out, env = walled
    r = _dc("-f", "compose.yaml", "-f", str(out), "config", "--format", "json", cwd=pkg, env=env)
    assert r.returncode == 0, r.stderr
    import json
    model = json.loads(r.stdout)
    assert list(model["services"]["app"]["networks"]) == ["pp-l4walltest-inner"]
    assert not model["services"]["app"].get("ports")
    assert model["networks"]["pp-l4walltest-inner"]["internal"] is True
    assert all(p["host_ip"] == "127.0.0.1" for s in model["services"].values() for p in s.get("ports") or [])


@docker
@pytest.mark.docker
def test_live_wall_holds_and_the_only_way_out_is_the_core_proxy(walled):
    z, pkg, out, env = walled
    r = _dc("-f", "compose.yaml", "-f", str(out), "up", "-d", "--quiet-pull", cwd=pkg, env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    direct = _dc("exec", "-T", "app", "curl", "-s", "-m", "5", "--noproxy", "*", "-k", "https://1.1.1.1",
                 cwd=pkg, env=env)
    assert direct.returncode != 0, "a walled app reached the internet directly"
    via = _dc("exec", "-T", "app", "curl", "-s", "-m", "10", "https://not-granted.example.com", cwd=pkg, env=env)
    assert via.returncode != 0
    lines = [x for x in z.bridge.ledger.read() if x["subject"] == "pkg:l4walltest"]
    assert any(x["host"] == "not-granted.example.com" and x["code"] in ("bridge_closed", "not_on_list") for x in lines), \
        "the request reached the Core's proxy through the gate, with the package's identity"
    assert dw.probe("l4walltest", core_port=8420)["held"] is True
