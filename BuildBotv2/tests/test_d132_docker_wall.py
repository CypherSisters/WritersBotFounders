"""D132: the Docker wall on plain Linux Docker, with no Docker needed. What the override says, finding the bridge's
address, a failed bind or no address leaves the Core running and refuses only the app, no credential is refused on the
second listener. The real engine is ``test_i2b_docker_live.py``."""

import base64
import json
import shutil
import socket
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from puffpai_core import wiring
from puffpai_core.steward import steward as steward_mod
from puffpai_core.steward.steward import StewardError
from puffpai_core.zone3 import docker_wall as dw
from puffpai_core.zone3 import service
from puffpai_core.zone3.proxy import Proxy
from puffpai_core.zone3.service import Zone3
from puffpai_core.zone3.toolsmith_host import ports as ports_mod
from puffpai_core.zone3.toolsmith_host.trials import Trials

URL = "http://walltest:s3cr3t-for-tests@127.0.0.1:8421"
MODEL = {"services": {"web": {"image": "x/web", "ports": ["8080:80"], "extra_hosts": ["evil:1.2.3.4"]},
                      "db": {"image": "x/db"}}}
NOT_HERE = "10.255.255.1"                     # private, and not an address of this computer: a bind fails


class _Tags(yaml.SafeLoader):
    pass


_Tags.add_multi_constructor("!", lambda loader, suffix, node: (
    suffix, loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode) else loader.construct_scalar(node)))


def _parsed(**kw) -> dict:
    return yaml.load(dw.render("walltest", MODEL, URL, host_proxy_port=9321, **kw), Loader=_Tags)


class Answers:
    """A stand-in for running ``docker``: answers by the first words after it, and records every call."""

    def __init__(self, *, info="Debian GNU/Linux 13 (trixie)", bridge='[{"Subnet":"172.18.0.0/16","Gateway":"172.18.0.1"}]',
                 info_code=0, bridge_code=0):
        self.info, self.bridge, self.info_code, self.bridge_code = info, bridge, info_code, bridge_code
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[1] == "info":
            return subprocess.CompletedProcess(args, self.info_code, self.info, "")
        return subprocess.CompletedProcess(args, self.bridge_code, self.bridge, "")


class FakeProxy:
    port = 9321

    def __init__(self, fail=None):
        self.fail, self.addresses = fail, []

    def listen_also(self, address):
        if self.fail:
            raise self.fail
        self.addresses.append(address)


@pytest.fixture
def plain_linux(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    monkeypatch.setattr(dw.shutil, "which", lambda name, **kw: "/usr/bin/docker")


# -- the override's text ----------------------------------------------------------------------------
def test_on_linux_the_gate_gets_the_host_and_the_apps_do_not():
    doc = _parsed(host_gateway=True)
    gate = doc["services"][dw.GATE]
    assert gate["extra_hosts"] == ["host.docker.internal:host-gateway"]
    assert gate["command"][1] == f"tcp-connect:host.docker.internal:9321,connect-timeout={dw.GATE_CONNECT_S}"
    for name in ("web", "db"):
        assert doc["services"][name]["extra_hosts"] == ("reset", [])
        assert list(doc["services"][name]["networks"][1]) == [dw.inner_network("walltest")]
    assert "extra_hosts" not in doc["services"]["pp-page-1"]         # a page gate stays off this computer too
    text = dw.render("walltest", MODEL, URL, host_gateway=True)
    assert text.count("host-gateway") == 1 and "0.0.0.0" not in text


def test_on_linux_the_inner_network_has_no_address_on_this_computer():
    nets = _parsed(host_gateway=True)["networks"]
    inner, outer = nets[dw.inner_network("walltest")], nets[dw.outer_network("walltest")]
    assert inner["internal"] is True and inner["driver_opts"] == {dw.NO_HOST_ADDRESS: "true"}
    assert "driver_opts" not in outer and "internal" not in outer


def test_docker_desktop_keeps_its_text():
    text = dw.render("walltest", MODEL, URL, host_proxy_port=9321)
    assert "extra_hosts: [" not in text and "host-gateway" not in text and "driver_opts" not in text
    assert '"tcp-connect:host.docker.internal:9321"]' in text        # no connect-timeout either


def test_override_passes_it_on(tmp_path):
    out = dw.override({"id": "walltest"}, tmp_path / "compose.yaml", proxy_url=URL, model=MODEL, host_gateway=True,
                      host_proxy_port=9321)
    assert "host-gateway" in out.read_text(encoding="utf-8")


# -- finding the bridge's address -------------------------------------------------------------------
def test_the_gateway_is_read_from_the_engine_and_not_assumed():
    assert dw.bridge_gateway(runner=Answers()) == "172.18.0.1"
    both = '[{"Subnet":"fd00::/64","Gateway":"fd00::1"},{"Subnet":"192.168.200.0/24","Gateway":"192.168.200.1"}]'
    assert dw.bridge_gateway(runner=Answers(bridge=both)) == "192.168.200.1"


@pytest.mark.parametrize("answer", ['[{"Subnet":"172.17.0.0/16"}]', "[]", "null", "not json", '[{"Gateway":"0.0.0.0"}]',
                                    '[{"Gateway":"127.0.0.1"}]', '[{"Gateway":"8.8.8.8"}]', '[{"Gateway":"169.254.1.1"}]',
                                    '[{"Gateway":"bad.address"}]', '["odd"]'])
def test_no_usable_gateway_is_no_answer(answer):
    assert dw.bridge_gateway(runner=Answers(bridge=answer)) == ""


def test_an_engine_that_fails_gives_no_gateway():
    assert dw.bridge_gateway(runner=Answers(bridge_code=1)) == ""

    def gone(*a, **k):
        raise FileNotFoundError("docker")
    assert dw.bridge_gateway(runner=gone) == ""


# -- when the listener appears ----------------------------------------------------------------------
def test_not_on_linux_nothing_is_asked_and_nothing_changes(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", False)
    run, proxy = Answers(), FakeProxy()
    assert dw.wall_kw(proxy, runner=run) == {"host_proxy_port": 9321}
    assert not run.calls and not proxy.addresses


def test_with_no_docker_program_nothing_is_asked(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    monkeypatch.setattr(dw.shutil, "which", lambda name, **kw: None)
    run, proxy = Answers(), FakeProxy()
    assert dw.wall_kw(proxy, runner=run) == {"host_proxy_port": 9321}
    assert not run.calls and not proxy.addresses


def test_plain_linux_listens_and_says_so(plain_linux):
    proxy = FakeProxy()
    assert dw.wall_kw(proxy, runner=Answers()) == {"host_proxy_port": 9321, "host_gateway": True}
    assert proxy.addresses == ["172.18.0.1"]


def test_docker_desktop_on_linux_stays_as_it_is(plain_linux):
    proxy = FakeProxy()
    assert dw.wall_kw(proxy, runner=Answers(info="Docker Desktop")) == {"host_proxy_port": 9321}
    assert not proxy.addresses


def test_an_engine_that_is_down_is_said_plainly(plain_linux):
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(FakeProxy(), runner=Answers(info_code=1, info=""))
    assert "engine is not answering" in str(e.value)


def test_a_user_outside_the_docker_group_is_told_so(plain_linux):
    def denied(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "permission denied while trying to connect to the Docker daemon socket")
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(FakeProxy(), runner=denied)
    assert "docker group" in str(e.value)


def test_a_docker_that_cannot_be_run_is_said_plainly(plain_linux):
    def gone(args, **kw):
        raise subprocess.TimeoutExpired(args, 20)
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(FakeProxy(), runner=gone)
    assert "engine is not answering" in str(e.value)


def test_no_address_is_said_plainly(plain_linux):
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(FakeProxy(), runner=Answers(bridge="[]"))
    assert "could not be found" in str(e.value) and "not started" in str(e.value)


@pytest.mark.parametrize("fail", [OSError(99, "Cannot assign requested address"), ValueError("no"), RuntimeError("no")])
def test_a_failed_bind_is_said_plainly(plain_linux, fail):
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(FakeProxy(fail=fail), runner=Answers())
    assert "172.18.0.1" in str(e.value) and "9321" in str(e.value) and "not started" in str(e.value)


# -- the second listener, on real sockets -----------------------------------------------------------
@pytest.fixture
def zone(state, monkeypatch):
    monkeypatch.setattr(service, "_CURRENT", service._CURRENT)        # back as it was after the test
    z = Zone3(state)
    z.start_proxy(0)
    yield z
    z.stop()


def _ask(address: str, port: int, auth: str | None = None) -> bytes:
    head = "CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n"
    if auth:
        head += f"Proxy-Authorization: {auth}\r\n"
    with socket.create_connection((address, port), 5) as s:
        s.settimeout(5)
        s.sendall((head + "\r\n").encode())
        return s.recv(4096)


def _basic(url: str) -> str:
    return "Basic " + base64.b64encode(url.split("//", 1)[1].rsplit("@", 1)[0].encode()).decode()


def _second_address(px: Proxy, address: str = "127.0.0.2") -> str:
    """127.0.0.2 stands in for the bridge's address: the same code path, and no real network."""
    try:
        px.listen_also(address)
    except OSError:
        pytest.skip("this computer has no 127.0.0.2")
    return address


def test_no_credential_is_refused_on_the_second_listener(zone):
    px = zone.proxy
    second = _second_address(px)
    assert _ask(second, px.port).startswith(b"HTTP/1.1 407")
    assert _ask(second, px.port, "Basic " + base64.b64encode(b"walltest:wrong").decode()).startswith(b"HTTP/1.1 407")
    lines = zone.bridge.ledger.read(limit=50)
    assert sum(x["code"] == "bad_credential" and x["outcome"] == "refused" for x in lines) == 2


def test_the_second_listener_runs_the_same_rules(zone):
    px = zone.proxy
    second = _second_address(px)
    url = zone.package_env("walltest")["HTTPS_PROXY"]
    # A real credential gets past the 407 and meets the Bridge's own decision, which is No: nothing is granted.
    assert _ask(second, px.port, _basic(url)).startswith(b"HTTP/1.1 403")
    assert _ask("127.0.0.1", px.port).startswith(b"HTTP/1.1 407")       # 127.0.0.1 stays as it was


def test_the_second_listener_stops_with_the_proxy(state, monkeypatch):
    monkeypatch.setattr(service, "_CURRENT", service._CURRENT)
    z = Zone3(state)
    z.start_proxy(0)
    px = z.proxy
    second = _second_address(px)
    port = px.port
    z.stop()
    with pytest.raises(OSError):
        socket.create_connection((second, port), 2)


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "8.8.8.8", "not an address"])
def test_it_never_listens_on_every_address_or_a_public_one(zone, address):
    with pytest.raises(ValueError):
        zone.proxy.listen_also(address)
    assert _ask("127.0.0.1", zone.proxy.port).startswith(b"HTTP/1.1 407")


def test_listen_also_needs_a_running_proxy(zone):
    px = Proxy(zone._proxy_decide, zone.bridge.ledger, zone.credentials, port=0)
    with pytest.raises(RuntimeError):
        px.listen_also("172.18.0.1")


def test_a_failed_bind_leaves_the_proxy_running(zone):
    with pytest.raises(OSError):
        zone.proxy.listen_also(NOT_HERE)
    assert _ask("127.0.0.1", zone.proxy.port).startswith(b"HTTP/1.1 407")


def test_a_real_failed_bind_through_the_wall_refuses_the_app_and_nothing_else(zone, plain_linux):
    with pytest.raises(dw.WallRefused) as e:
        dw.wall_kw(zone.proxy, runner=Answers(bridge=f'[{{"Gateway":"{NOT_HERE}"}}]'))
    assert NOT_HERE in str(e.value)
    assert _ask("127.0.0.1", zone.proxy.port).startswith(b"HTTP/1.1 407")


def test_a_port_someone_else_holds_on_that_address_is_refused(zone):
    second = _second_address(zone.proxy)
    zone.proxy.listen_also(second)                                     # the same address again is no change
    px2 = Proxy(zone._proxy_decide, zone.bridge.ledger, zone.credentials, port=0)
    px2.start()
    try:
        px2.port = zone.proxy.port
        with pytest.raises(OSError):
            px2.listen_also(second)
    finally:
        px2.stop()


# -- the start ---------------------------------------------------------------------------------------
class _Compose:
    """Steward's ``jobs.run``: ``up`` passes, the gate's check gives *check_code*. Records every compose call."""

    def __init__(self, check_code=0):
        self.check_code, self.calls = check_code, []

    def __call__(self, argv, cwd, env, log, timeout):
        self.calls.append(argv)
        return self.check_code if "exec" in argv else 0


@pytest.fixture
def start(core, tmp_path, monkeypatch):
    """Steward's real ``_compose_up`` on a small folder, never a real ``docker``."""
    (tmp_path / "compose.yaml").write_text("services:\n  web: {image: x/web}\n", encoding="utf-8")
    monkeypatch.setattr(dw, "load_model", lambda *a, **k: {"services": {"web": {"image": "x/web"}}})
    monkeypatch.setattr(shutil, "which", lambda name, **kw: "/usr/bin/docker")
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)

    def go():
        return core.steward._compose_up("walltest", tmp_path, {"id": "walltest"}, {"file": "compose.yaml"},
                                        {"HTTPS_PROXY": URL}, 18555, None)
    return go


def test_a_gate_that_cannot_reach_the_proxy_says_so_and_the_app_goes_down(core, start, monkeypatch):
    runs = _Compose(check_code=1)
    monkeypatch.setattr(steward_mod.jobs, "run", runs)
    core.steward.docker_wall_kw = lambda id: {"host_proxy_port": 9321, "host_gateway": True}
    with pytest.raises(StewardError) as e:
        start()
    assert "can't reach the Bridge proxy" in str(e.value) and "firewall" in str(e.value)
    assert "does not change your firewall" in str(e.value) and "was stopped" in str(e.value)
    assert chr(0x2014) not in str(e.value)
    checks = [c for c in runs.calls if "exec" in c]
    assert len(checks) == 2 and "down" in runs.calls[-1]
    assert "connect-timeout" in checks[0][-1] and "9321" in checks[0][-1]
    assert [c for c in runs.calls if c[-2:] == ["up", "-d"]]


def test_a_gate_that_reaches_the_proxy_lets_the_app_run(core, start, monkeypatch):
    runs = _Compose()
    monkeypatch.setattr(steward_mod.jobs, "run", runs)
    core.steward.docker_wall_kw = lambda id: {"host_proxy_port": 9321, "host_gateway": True}
    assert start().kind == "docker_compose"
    assert sum("exec" in c for c in runs.calls) == 1 and "down" not in runs.calls[-1]


def test_docker_desktop_gets_no_gate_check(core, start, monkeypatch):
    runs = _Compose(check_code=1)
    monkeypatch.setattr(steward_mod.jobs, "run", runs)
    core.steward.docker_wall_kw = lambda id: {"host_proxy_port": 9321}
    assert start().kind == "docker_compose"
    assert not any("exec" in c for c in runs.calls)


def test_a_refusal_before_compose_stops_the_app_and_runs_no_compose(core, start, monkeypatch):
    runs = _Compose()
    monkeypatch.setattr(steward_mod.jobs, "run", runs)

    def refuse(id):
        raise dw.WallRefused(["The address of Docker's bridge network could not be found."])
    core.steward.docker_wall_kw = refuse
    with pytest.raises(StewardError) as e:
        start()
    assert "could not be found" in str(e.value) and not runs.calls


def test_with_no_docker_program_it_says_what_it_always_said(core, tmp_path, monkeypatch, state):
    """Through the real wiring, on Linux: no ``docker``, so no listener, and the same words as ever."""
    (tmp_path / "compose.yaml").write_text("services:\n  web: {image: x/web}\n", encoding="utf-8")
    monkeypatch.setattr(service, "_CURRENT", service._CURRENT)
    monkeypatch.setattr(dw, "_LINUX", True)
    monkeypatch.setattr(dw, "load_model", lambda *a, **k: {"services": {"web": {"image": "x/web"}}})
    monkeypatch.setattr(shutil, "which", lambda name, **kw: None)
    z = Zone3(state)
    z.start_proxy(0)
    core.steward.docker_wall_kw = lambda id: wiring.docker_wall_kw(z)
    try:
        with pytest.raises(StewardError) as e:
            core.steward._compose_up("walltest", tmp_path, {"id": "walltest"}, {"file": "compose.yaml"},
                                     {"HTTPS_PROXY": URL}, 18555, None)
        assert str(e.value) == "Docker is not installed on this computer."
        assert not z.proxy._also
    finally:
        z.stop()


# -- the gate check and the probe ---------------------------------------------------------------------
def test_the_gate_check_is_a_bare_connection_and_retries(monkeypatch):
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    codes, seen = [1, 0], []

    def run(args):
        seen.append(args)
        return codes.pop(0)
    assert dw.gate_problem(run, 9321) == "" and len(seen) == 2
    assert seen[0][:3] == ["exec", "-T", dw.GATE] and "Basic" not in json.dumps(seen[0])


def _probe_runner(*, inner, connects=(), inspect_code=0, bridge='[{"Gateway":"172.18.0.1"}]'):
    tried = []

    def run(args, **kw):
        if args[1:3] == ["network", "inspect"]:
            answer = bridge if args[3] == "bridge" else json.dumps([{"Gateway": inner}])
            return subprocess.CompletedProcess(args, inspect_code, answer, "")
        tried.append(args[-1])
        return subprocess.CompletedProcess(args, 0 if any(c in args[-1] for c in connects) else 7, "", "")
    run.tried = tried
    return run


def test_the_probe_holds_on_linux_only_when_this_computer_is_not_on_the_inner_network(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    run = _probe_runner(inner=NOT_HERE)
    assert dw.probe("walltest", core_port=1234, proxy_port=9321, runner=run) == {"held": True, "reached": []}
    assert f"http://{NOT_HERE}:1234" in run.tried and "http://172.18.0.1:9321" in run.tried


def test_the_probe_sees_a_gateway_that_is_this_computer_even_with_the_port_closed(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    got = dw.probe("walltest", runner=_probe_runner(inner="127.0.0.1"))
    assert got == {"held": False, "reached": ["this_computer_by_inner_gateway"]}


def test_the_probe_sees_the_proxy_reached_without_the_gate(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    got = dw.probe("walltest", proxy_port=9321, runner=_probe_runner(inner=NOT_HERE, connects=[":9321"]))
    assert got == {"held": False, "reached": ["proxy_without_the_gate"]}


def test_the_probe_fails_closed_when_it_cannot_read_the_network(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", True)
    got = dw.probe("walltest", runner=_probe_runner(inner=NOT_HERE, inspect_code=1))
    assert got == {"held": False, "reached": ["probe_failed"]}


def test_the_probe_off_linux_is_as_it_was(monkeypatch):
    monkeypatch.setattr(dw, "_LINUX", False)
    run = _probe_runner(inner=NOT_HERE)
    assert dw.probe("walltest", core_port=1234, runner=run) == {"held": True, "reached": []}
    assert run.tried == ["https://1.1.1.1", "https://example.com", "http://host.docker.internal:1234",
                         "http://192.168.65.254:1234"]


# -- Toolsmith's try goes the same way ---------------------------------------------------------------
def _find_with(monkeypatch, **fns):
    real = ports_mod.find
    monkeypatch.setattr(ports_mod, "find", lambda name: fns.get(name.rsplit(".", 1)[-1], real(name)))


def test_a_try_gets_the_real_port_and_the_listener(tmp_path, monkeypatch):
    (tmp_path / "compose.yaml").write_text("services:\n  web: {image: x/web}\n", encoding="utf-8")
    proxy = FakeProxy()
    seen = []
    monkeypatch.setattr(dw, "load_model", lambda *a, **k: {"services": {"web": {"image": "x/web"}}})

    def wall_kw(p):
        seen.append(p)
        return {"host_proxy_port": p.port, "host_gateway": True}
    _find_with(monkeypatch, wall_kw=wall_kw)
    zone = SimpleNamespace(proxy=proxy, credentials=SimpleNamespace(issue=lambda pid: URL))
    monkeypatch.setattr(service, "_CURRENT", zone)
    got = ports_mod.Ports(tmp_path, zone=zone).docker_override({"id": "walltest"}, tmp_path / "compose.yaml")
    text = got.read_text(encoding="utf-8")
    assert seen == [proxy] and "host-gateway" in text and "tcp-connect:host.docker.internal:9321" in text


def test_a_refused_try_is_a_refusal_in_plain_words(tmp_path, monkeypatch):
    def wall_kw(p):
        raise dw.WallRefused(["The address of Docker's bridge network could not be found."])
    _find_with(monkeypatch, wall_kw=wall_kw)
    zone = SimpleNamespace(proxy=FakeProxy())
    with pytest.raises(ports_mod.Refused) as e:
        ports_mod.Ports(tmp_path, zone=zone).docker_override({"id": "walltest"}, tmp_path / "compose.yaml")
    assert "could not be found" in str(e.value)


@pytest.mark.parametrize("kw, code, said", [({"host_proxy_port": 9321, "host_gateway": True}, 1, "can't reach the Bridge"),
                                            ({"host_proxy_port": 9321, "host_gateway": True}, 0, ""),
                                            ({"host_proxy_port": 9321}, 1, "")])
def test_a_tries_gate_check_is_plain_linux_only(tmp_path, monkeypatch, kw, code, said):
    monkeypatch.setattr(dw.time, "sleep", lambda s: None)
    _find_with(monkeypatch, wall_kw=lambda p: kw)
    got = ports_mod.Ports(tmp_path, zone=SimpleNamespace(proxy=FakeProxy())).docker_gate_problem(lambda args: code)
    assert (said in got) if said else got == ""


def _rec(tmp_path, runs):
    def core_run(argv, cwd, line, *, limit_s=None):
        runs.append(argv)
        return {"ok": True, "exit": 1}
    return SimpleNamespace(compose={"project": "puffpai-t", "files": ["compose.yaml", ".buildbot/compose.puffpai.yml"]},
                           folder=tmp_path, broker=SimpleNamespace(docker="docker", core_run=core_run))


def test_a_try_is_told_when_its_gate_cannot_reach_the_proxy(tmp_path):
    runs, seen = [], []

    def ask(run):
        seen.append(run(["exec", "-T", dw.GATE, "socat"]))
        return "This Docker app can't reach the Bridge proxy."
    got = {"exit": 0, "said": "Started."}
    Trials._gate_note(SimpleNamespace(ports=SimpleNamespace(docker_gate_problem=ask)), _rec(tmp_path, runs), ["up", "-d"], got)
    assert got["said"] == "Started.\n\nThis Docker app can't reach the Bridge proxy." and seen == [1]
    assert runs[0][:4] == ["docker", "compose", "-p", "puffpai-t"] and runs[0][-4:-1] == ["exec", "-T", dw.GATE]


@pytest.mark.parametrize("rest, exit_code", [(["ps"], 0), (["up", "-d"], 1)])
def test_a_try_only_checks_after_a_good_up(tmp_path, rest, exit_code):
    def ask(run):
        raise AssertionError("not asked")
    got = {"exit": exit_code, "said": "x"}
    Trials._gate_note(SimpleNamespace(ports=SimpleNamespace(docker_gate_problem=ask)), _rec(tmp_path, []), rest, got)
    assert got["said"] == "x"
