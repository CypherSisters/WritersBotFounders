"""Floor items held by lane L1: writes only in its folders (02, check item 5), the MCP endpoint
refuses without the package token (02, D48d), no Drawbridge means no (X26, X24), Stop leaves no
process (D18), packages listen on 127.0.0.1 only (X17), raw readers get no token, keys or proxy (X13)."""

import json
import os

import pytest

from conftest import HELLO, wait_for, yes
from puffpai_core.packages import mcp_tools
from puffpai_core.packages.check import FAIL, check
from puffpai_core.packages.manager import AddError
from puffpai_core.steward.jobs import pid_alive


def test_a_package_that_writes_outside_its_folders_fails_check(drawbridge, hello_copy):
    (hello_copy / "evil.py").write_text(
        "import os, runpy\n"
        "open(os.path.join(os.getcwd(), '..', 'escape.txt'), 'w').write('out')\n"
        "runpy.run_path('server.py', run_name='__main__')\n", encoding="utf-8")
    data = json.loads((hello_copy / "puffpai.json").read_text())
    data["run"] = {"default": {"type": "command", "start": ["{python}", "evil.py"]}}
    (hello_copy / "puffpai.json").write_text(json.dumps(data))
    report = check(hello_copy)
    five = [(s, w) for lv, i, s, w in report.items if i == 5]
    assert five[0][0] == FAIL and "packages/escape.txt" in five[0][1]
    assert report.passed() == 0


def test_mcp_refuses_without_the_token(core, drawbridge, hello_copy):
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    port = core.registry.get("hello-tool")["port"]
    url = f"http://127.0.0.1:{port}/mcp"
    manifest = core.steward.manifest("hello-tool")
    values = {"port": str(port)}
    assert wait_for(lambda: mcp_tools.refuses_without_token(url), 30)
    with pytest.raises(mcp_tools.McpError):
        mcp_tools.list_tools(manifest, values, None, timeout_s=10)
    with pytest.raises(mcp_tools.McpError):
        mcp_tools.list_tools(manifest, values, "ppt_not-its-token", timeout_s=10)
    names = {t["name"] for t in mcp_tools.list_tools(manifest, values, core.steward.token("hello-tool"))}
    assert names == {"say_hello", "write_note"}


def test_no_drawbridge_means_nothing_is_added(core, hello_copy, state, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "puffpai_core.zone3.drawbridge", None)     # lane L4's code not here
    with pytest.raises(AddError) as e:
        core.packages.add(str(hello_copy))
    assert e.value.status == 403 and core.registry.list() == [] and core.consent.list() == []
    assert list((state / "packages" / ".staging").iterdir()) == []


def test_drawbridge_gone_between_check_and_yes_means_no(core, drawbridge, hello_copy, monkeypatch, state):
    staged = core.packages.add(str(hello_copy))
    import sys
    monkeypatch.setitem(sys.modules, "puffpai_core.zone3.drawbridge", None)     # lane L4's code gone
    result = yes(core, staged)
    assert result["status"] == "failed" and "not installed" in " ".join(result["problems"])
    assert core.registry.get("hello-tool") is None and not (state / "packages" / "hello-tool").exists()


def test_a_crashing_or_odd_drawbridge_is_a_no(core, hello_copy, monkeypatch):
    from conftest import drawbridge_double
    for answer in (lambda p, m: 1 / 0, lambda p, m: None, lambda p, m: {"verdict": "pass"},
                   lambda p, m: {"verdict": "block", "reasons": ["hidden_loader"]},
                   lambda p, m: {"verdict": "allow", "tree_sha": "0" * 64}, lambda p, m: {"verdict": "allow", "tree_sha": ""}):
        drawbridge_double(monkeypatch, answer)
        with pytest.raises(AddError):
            core.packages.add(str(hello_copy))
    assert core.registry.list() == []


def test_stop_leaves_no_process(core, drawbridge, hello_copy, tmp_path):
    (hello_copy / "spawner.py").write_text(
        "import subprocess, sys, runpy\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "runpy.run_path('server.py', run_name='__main__')\n", encoding="utf-8")
    data = json.loads((hello_copy / "puffpai.json").read_text())
    data["run"] = {"default": {"type": "command", "start": ["{python}", "spawner.py"]}}
    (hello_copy / "puffpai.json").write_text(json.dumps(data))
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    # Windows: the venv's launcher, its real Python and the child. Elsewhere a venv's python has no launcher.
    want = 3 if os.name == "nt" else 2
    pids = wait_for(lambda: len(core.steward.pids("hello-tool")) >= want and core.steward.pids("hello-tool"), 20)
    assert pids, "the child it started is in its job"
    assert core.steward.stop_all() == ["hello-tool"]                    # the Core stops: its packages stop
    assert not any(pid_alive(p) for p in pids)


def test_listening_on_all_networks_is_found():
    from puffpai_core.steward import ports
    assert ports.network_reachable(["0.0.0.0"]) and ports.network_reachable(["::"])
    assert not ports.network_reachable(["127.0.0.1", "::1"])


def test_raw_readers_get_no_token_keys_or_proxy(core):
    for slot in ("zone2.runtime", "zone2.vision", "zone3.toolsmith-model"):
        manifest = {"kind": "provider", "reads": "none", "provides": [{"slot": slot, "contract": 1}]}
        env = core.steward.env("p", manifest, 18500, "ppt_x", {"KEY": "v"})
        assert not {"PUFFPAI_TOKEN", "KEY", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"} & set(env), slot
