"""Lane L2: model settings (one file, X12), the recommender (D36(e), T-PB005), the Ollama adapter and brain states (D36(d))."""

import copy
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from puffpai_core.zone2 import catalog, models
from puffpai_core.zone2 import settings as ms
from puffpai_core.zone2.errors import BrainError
from puffpai_core.zone2.ollama import OllamaRuntime


def orin(ram=61.0, free=857.0, soc="NVIDIA Jetson AGX Orin Developer Kit"):
    return {"memory": {"total_gb": ram}, "disk": {"free_gb": free}, "hardware_class": "workstation",
            "soc": {"model": soc}, "accelerator": {"kind": "jetson_integrated", "name": "NVIDIA integrated (Tegra)"},
            "cpu": {"model": "ARMv8 Processor rev 1 (v8l)"}}


def rec(menu):
    return menu["recommended"]


# -- T-PB005-benchmark-pick (03 1.6) -----------------------------------------

def test_pb005_benchmarked_board_wins():
    menu = catalog.fit_menu(orin(), installed=["qwen3.5:9b"])
    assert rec(menu)["id"] == "qwen3:30b-a3b" and rec(menu)["rule"] == 1
    assert "19 GB download" in rec(menu)["reason"] and "Jetson AGX Orin 64GB" in rec(menu)["reason"]
    assert menu["already_here"][0]["id"] == "qwen3.5:9b"
    assert [o["id"] for o in menu["options"] if o["recommended"]] == ["qwen3:30b-a3b"]


def test_pb005_not_enough_disk_falls_to_rule_2():
    menu = catalog.fit_menu(orin(free=20.0), installed=[])          # 20 < 19 x 1.2 = 22.8
    assert rec(menu)["rule"] == 2 and rec(menu)["id"] == "qwen3.5:9b"


def test_pb005_another_board_gets_no_benchmark_numbers():
    menu = catalog.fit_menu(orin(soc="Raspberry Pi 5 Model B"), installed=[])
    assert rec(menu)["rule"] == 2
    assert all(o["benchmark"] is None for o in menu["options"])


def test_any_benchmarked_silicon_works_windows_first():
    cat = copy.deepcopy(catalog.load_catalog())
    cat["silicon"]["rtx_2000_ada_8gb"] = {"label": "RTX 2000 Ada", "hardware_class": "workstation"}
    row = next(m for m in cat["models"] if m["id"] == "qwen3:30b-a3b")
    row["benchmarks"]["rtx_2000_ada_8gb"] = {"chat_range_s": "2-9"}
    laptop = {"memory": {"total_gb": 63.7}, "disk": {"free_gb": 127.0}, "hardware_class": "workstation",
              "soc": {"model": None}, "accelerator": {"kind": "nvidia_dgpu", "name": "NVIDIA RTX 2000 Ada Generation Laptop GPU"},
              "cpu": {"model": "13th Gen Intel(R) Core(TM) i7-13850HX"}}
    menu = catalog.fit_menu(laptop, installed=[], catalog=cat)
    assert rec(menu)["id"] == "qwen3:30b-a3b" and rec(menu)["rule"] == 1
    assert "19 GB download" in rec(menu)["reason"] and "RTX 2000 Ada" in rec(menu)["reason"]
    assert catalog.fit_menu({**laptop, "accelerator": {"kind": "unknown"}}, [], catalog=cat)["recommended"]["rule"] == 2


def test_installed_says_already_downloaded_and_unknown_ram_asks():
    laptop = {"memory": {"total_gb": 32.0}, "disk": {"free_gb": 100.0}, "hardware_class": "high"}
    menu = catalog.fit_menu(laptop, installed=["qwen3.5:9b"])
    assert rec(menu)["id"] == "qwen3.5:9b" and "Already downloaded" in rec(menu)["reason"]
    assert catalog.fit_menu({"memory": {}}, [])["message"].startswith("We could not read")
    assert models.menu(laptop, installed_models=[])[0]["id"] == "qwen3:30b-a3b"


def test_profiles_give_tool_budget_context_and_think():
    p = catalog.get_profile("qwen2.5:7b-instruct-q4_K_M")
    assert p["inherited_from"] == "qwen2.5:7b-instruct" and p["basis"] == "conservative-default"
    assert catalog.get_profile("nobody:1b")["tool_budget"] == 16
    assert catalog.get_profile("qwen3:30b-a3b")["tool_budget"] > catalog.get_profile("llama3.2:1b")["tool_budget"]


# -- one settings place, X12 ---------------------------------------------------

def test_settings_address_comes_from_ollama_host(state, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11435")
    assert ms.load(state).address == "http://127.0.0.1:11435"
    monkeypatch.delenv("OLLAMA_HOST")
    assert ms.load(state).address == ms.OLLAMA_DEFAULT
    s = ms.save(state, model="qwen2.5:3b-instruct", tool_budget=100, context=999999)
    assert s.tools_allowed() == 12 and s.context_size() == 4096 and s.think() is False
    assert json.loads(ms.path(state).read_text())["model"] == "qwen2.5:3b-instruct"
    with pytest.raises(ValueError):
        ms.save(state, colour="blue")


@pytest.mark.parametrize("address,lan,kind", [
    ("http://127.0.0.1:11435", [], "local"), ("http://localhost:11434", [], "local"), ("http://[::1]:1", [], "local"),
    ("http://192.168.1.50:11434", ["192.168.1.50"], "lan"), ("http://192.168.1.50:11434", [], "refused"),
    ("http://93.184.216.34:11434", ["93.184.216.34"], "refused"), ("https://api.example.com", [], "refused"),
])
def test_x12_address_kinds(address, lan, kind):
    assert ms.address_kind(address, lan) == kind


# -- the Ollama adapter against a fake Ollama ----------------------------------

class FakeOllama(BaseHTTPRequestHandler):
    tags = ["qwen2.5:3b-instruct"]
    loaded: list = []
    bodies: list = []
    delay = 0.0
    reply: dict = {"message": {"role": "assistant", "content": "Hi."}, "done": True, "done_reason": "stop"}

    def log_message(self, *a):
        pass

    def _send(self, code, obj, lines=None):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if lines is not None:
            for line in lines:
                self.wfile.write((json.dumps(line) + "\n").encode())
        else:
            self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": n, "size": 2_000_000_000} for n in self.tags]})
        elif self.path == "/api/ps":
            self._send(200, {"models": [{"name": n} for n in self.loaded]})
        else:
            self._send(200, {"version": "0.0-test"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        type(self).bodies.append(body)
        time.sleep(self.delay)
        if body.get("model") not in self.tags:
            return self._send(404, {"error": f"model '{body.get('model')}' not found, try pulling it first"})
        if body.get("stream"):
            return self._send(200, None, lines=[{"message": {"content": "Hel"}}, {"message": {"content": "lo."}},
                                                {"message": {"tool_calls": [{"function": {"name": "a__b",
                                                                                          "arguments": {"x": 1}}}]}},
                                                {"done": True, "done_reason": "stop"}])
        self._send(200, self.reply)


@pytest.fixture
def ollama():
    FakeOllama.bodies, FakeOllama.delay, FakeOllama.loaded = [], 0.0, []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_adapter_chat_sends_think_false_tools_and_context(ollama):
    rt = OllamaRuntime(ollama, timeout_s=5)
    got = rt.chat([{"role": "user", "content": "hi"}], model="qwen2.5:3b-instruct", tools=[{"type": "function"}],
                  options={"num_ctx": 4096})
    assert got == {"text": "Hi.", "tool_calls": [], "done_reason": "stop", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    body = FakeOllama.bodies[-1]
    assert body["think"] is False and body["tools"] and body["options"] == {"num_ctx": 4096} and body["stream"] is False


def test_adapter_streams_words_and_tool_calls(ollama):
    rt = OllamaRuntime(ollama, timeout_s=5)
    words = []
    got = rt.chat([], model="qwen2.5:3b-instruct", on_token=words.append)
    assert words == ["Hel", "lo."] and got["text"] == "Hello."
    assert got["tool_calls"][0]["name"] == "a__b" and got["tool_calls"][0]["arguments"] == {"x": 1}
    assert "".join(rt.chat_stream([], model="qwen2.5:3b-instruct")) == "Hello."


def test_brain_states_are_named_and_distinct(ollama):
    rt = OllamaRuntime(ollama, timeout_s=0.5)
    assert rt.state("qwen3:30b-a3b")["state"] == "not_downloaded"
    with pytest.raises(BrainError) as err:
        rt.chat([], model="qwen3:30b-a3b")
    assert err.value.code == "not_downloaded" and "did not answer in time" not in err.value.message
    assert rt.state("qwen2.5:3b-instruct")["state"] == "ready"
    FakeOllama.delay = 1.5
    with pytest.raises(BrainError) as err:
        rt.chat([], model="qwen2.5:3b-instruct")
    assert err.value.code == "timed_out" and "did not answer in time" in err.value.message
    FakeOllama.delay = 0.0
    assert rt.state("qwen2.5:3b-instruct")["state"] == "timed_out"
    rt.chat([], model="qwen2.5:3b-instruct")
    assert rt.state("qwen2.5:3b-instruct")["state"] == "ready"
    rt._pulls["qwen3:30b-a3b"] = {"done_bytes": 4_000_000_000, "total_bytes": 19_000_000_000}
    assert rt.state("qwen3:30b-a3b") == {**rt.state("qwen3:30b-a3b"), "state": "not_downloaded",
                                          "done_gb": 4.0, "size_gb": 19.0}
    rt._loading.add("qwen2.5:3b-instruct")
    assert rt.state("qwen2.5:3b-instruct")["state"] == "loading"


def test_server_down_is_an_error_with_the_real_reason():
    rt = OllamaRuntime("http://127.0.0.1:9", timeout_s=2)
    st = rt.state("qwen2.5:3b-instruct")
    assert st["state"] == "error" and st["reason"] == "the model server is not running"
    with pytest.raises(BrainError) as err:
        rt.chat([], model="qwen2.5:3b-instruct")
    assert err.value.code == "error" and "not running" in err.value.message
    with pytest.raises(BrainError) as err:
        rt.chat([], model="")
    assert err.value.code == "no_model"
