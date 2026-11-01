"""Caretaker: health probes, tools into the Registry, restarts, broken, the after-start check, safe mode."""

import pytest

from conftest import wait_for, yes


@pytest.fixture
def running(core, drawbridge, hello_copy):
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    assert core.registry.get("hello-tool")["status"] == "running"
    return core


def _healthy(core):
    return wait_for(lambda: core.caretaker.check("hello-tool")["health"]["ok"], 60, 0.5)


def test_health_goes_ok_and_tools_reach_the_registry(running):
    assert _healthy(running)
    row = running.registry.get("hello-tool")
    assert row["health"]["reason"] == "Its page answers (200)."
    tools = {a["tool"]: a for a in row["abilities"]}
    assert set(tools) == {"hello-tool.say_hello", "hello-tool.write_note"}
    assert tools["hello-tool.write_note"]["changes"] is True and tools["hello-tool.say_hello"]["changes"] is False
    assert "hello" in tools["hello-tool.say_hello"]["description"].lower()
    assert running.registry.validate(tools) == []


def test_health_needs_the_package_token(running):
    from puffpai_core.caretaker.caretaker import _http_status
    assert _healthy(running)
    url = f"http://127.0.0.1:{running.registry.get('hello-tool')['port']}/health"
    assert _http_status(url, None) == 401                                # D48d: every route needs the token
    assert _http_status(url, running.steward.token("hello-tool")) == 200


def test_a_crash_is_restarted_then_broken(running):
    ct = running.caretaker
    ct.tries, ct.backoff_s = 1, 0.01
    assert _healthy(running)
    running.steward.info("hello-tool").job.terminate()
    row = ct.check("hello-tool")                                       # restart 1 of 1
    assert row["status"] == "running" and running.steward.alive("hello-tool")
    running.steward.info("hello-tool").job.terminate()
    row = ct.check("hello-tool")                                       # past its tries
    assert row["status"] == "broken" and "Stopped after 1 restarts" in row["health"]["reason"]
    assert not running.steward.alive("hello-tool")


def test_a_package_she_stopped_is_left_alone(running):
    running.steward.stop("hello-tool")
    row = running.caretaker.check("hello-tool")
    assert row["status"] == "stopped" and not running.steward.alive("hello-tool")


def test_listening_on_all_networks_is_stopped(running, monkeypatch):
    from puffpai_core.steward import ports
    monkeypatch.setattr(ports, "job_listeners", lambda pids: [("0.0.0.0", 18555)])
    row = wait_for(lambda: running.caretaker.check("hello-tool")["status"] == "broken" and
                   running.registry.get("hello-tool"), 60, 0.5)
    assert "all networks (0.0.0.0:18555)" in row["health"]["reason"] and not running.steward.alive("hello-tool")


def test_the_loop_runs(running):
    running.caretaker.tick_s = 0.2
    running.caretaker.start()
    assert wait_for(lambda: running.registry.get("hello-tool")["health"]["ok"], 60)
    running.caretaker.stop()


def test_selftest_safe_mode(core, monkeypatch):
    events = []
    core.bus.subscribe("core.selftest", events.append)
    import sys
    with monkeypatch.context() as m:
        m.setitem(sys.modules, "puffpai_core.zone3.drawbridge", None)     # lane L4's code not here
        report = core.caretaker.selftest()
    assert report["mode"] == "safe" and report["results"] == {"X4": "fail", "X10": "pass"}
    assert core.caretaker.in_safe_mode and "Drawbridge is not here" in report["reasons"][0]
    assert events[-1]["payload"]["mode"] == "safe"
    assert core.caretaker.selftest()["mode"] == "normal" and not core.caretaker.in_safe_mode   # the real one
