"""Steward: ports, placeholders, environment, start, stop, start again after a Core restart."""

import sys
import os
import json

import pytest

from conftest import HELLO, manifest_sha, wait_for, yes
from puffpai_core.steward import StewardError, fill, split_command, walled
from puffpai_core.steward import ports
from puffpai_core.steward.jobs import pid_alive


@pytest.fixture
def installed(core, drawbridge, hello_copy):
    core.packages.start_after_install = False
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    return core


def test_free_port_is_in_range_and_never_reserved(state):
    reserved = ports.reserved_ports(state)
    assert {8420, 8421, 11435} <= reserved
    assert json.loads((state / "settings" / "ports.json").read_text())["reserved"]     # seeded for the owner
    (state / "settings" / "ports.json").write_text(json.dumps({"reserved": list(range(18420, 18990))}))
    for _ in range(5):
        port = ports.free_port(state)
        assert 18990 <= port <= 18999


def test_split_and_fill():
    assert split_command('{python} -m pip install -r "my reqs.txt"') == ["{python}", "-m", "pip", "install", "-r", "my reqs.txt"]
    assert split_command(r"{python} scripts\setup.py") == ["{python}", r"scripts\setup.py"]
    assert fill("http://127.0.0.1:{port}/x", {"port": "18500"}) == "http://127.0.0.1:18500/x"


def test_env_for_a_package_and_for_a_walled_reader(core, monkeypatch):
    monkeypatch.setenv("PUFFPAI_TOKEN", "ppt_leaked")
    monkeypatch.setenv("HTTPS_PROXY", "http://evil:1")
    env = core.steward.env("x", {"kind": "tool"}, 18500, "ppt_new", {"NASA_API_KEY": "k"})
    assert env["PUFFPAI_TOKEN"] == "ppt_new" and env["PUFFPAI_PORT"] == "18500"
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://127.0.0.1:9" and env["NASA_API_KEY"] == "k"   # no proxy running
    plain = core.steward.proxy_env
    core.steward.proxy_env = None                                    # a Steward with no Zone 3 wired
    assert core.steward.env("x", {"kind": "tool"}, 18500, "t", {})["HTTPS_PROXY"] == "http://127.0.0.1:9"   # nowhere
    core.steward.proxy_env = plain
    assert env["PUFFPAI_API"] == "http://127.0.0.1:18419" and env["PUFFPAI_DATA"].endswith(os.path.join("data", "x"))
    raw = {"kind": "provider", "reads": "raw", "provides": [{"slot": "zone2.runtime", "contract": 1}]}
    assert walled(raw)
    env = core.steward.env("x", raw, 18500, "ppt_new", {"NASA_API_KEY": "k"})
    assert not {"PUFFPAI_TOKEN", "NASA_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy"} & set(env)


def test_start_and_stop_leave_nothing(installed):
    s = installed.steward
    row = s.start("hello-tool")
    assert row["status"] == "running" and 18420 <= row["port"] <= 18999
    assert wait_for(lambda: len(s.pids("hello-tool")) >= 1, 20)
    pids = s.pids("hello-tool")
    assert s.token("hello-tool", ) and installed.tokens.lookup(s.token("hello-tool")).package_id == "hello-tool"
    token = s.token("hello-tool")
    assert s.stop("hello-tool") is True
    assert not any(pid_alive(p) for p in pids)
    assert installed.registry.get("hello-tool")["status"] == "stopped"
    assert installed.tokens.lookup(token) is None                     # its token stops working
    log = s.log_path("hello-tool").read_text(encoding="utf-8")
    assert "start on port" in log and "stopped" in log and "ppt_" not in log


def test_core_stop_and_start_again(installed):
    s = installed.steward
    s.start("hello-tool")
    assert s.stop_all() == ["hello-tool"] and not s.alive("hello-tool")
    s.start_saved(background=False)
    assert s.alive("hello-tool") and installed.registry.get("hello-tool")["status"] == "running"


def test_a_process_that_dies_at_once_is_broken(installed, state):
    folder = state / "packages" / "hello-tool"
    (folder / "server.py").write_text("raise SystemExit(4)\n", encoding="utf-8")
    with pytest.raises(StewardError) as e:
        installed.steward.start("hello-tool")
    assert "exit 4" in str(e.value) and installed.registry.get("hello-tool")["status"] == "broken"


@pytest.mark.parametrize("key", ["windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "linux", "default"])
def test_docker_needs_the_wall(installed, state, key):
    folder = state / "packages" / "hello-tool"
    data = json.loads((folder / "puffpai.json").read_text())
    data["run"] = {key: {"type": "docker_compose", "file": "compose.yaml"}}
    (folder / "puffpai.json").write_text(json.dumps(data))
    # as if she said Yes to this manifest (an update): a changed file is refused before the wall (part D)
    installed.registry.update("hello-tool", manifest_sha=manifest_sha(folder))
    with pytest.raises(StewardError) as e:
        installed.steward.start("hello-tool")
    assert "Docker wall" in str(e.value)


def test_community_raw_reader_stays_off(core, drawbridge, hello_copy, archivist):
    data = json.loads((hello_copy / "puffpai.json").read_text())
    data.update(kind="provider", provides=[{"slot": "zone2.embedder", "contract": 1}], reads="raw")
    data.pop("abilities")
    (hello_copy / "puffpai.json").write_text(json.dumps(data))
    core.packages.start_after_install = False
    assert yes(core, core.packages.add(str(hello_copy)), factor="pin", pin="4321")["status"] == "installed"
    core.registry.update("hello-tool", origin="github:https://github.com/x/y")
    with pytest.raises(StewardError) as e:
        core.steward.start("hello-tool")
    assert "turn it on" in str(e.value)
