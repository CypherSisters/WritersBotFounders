"""PUFFPAI_ONLY_MODEL: one model and no downloads, for a bot that shares its Ollama (D132)."""

import pytest

from puffpai_core.zone2 import models, ollama
from puffpai_core.zone2.errors import BrainError

CENSUS = {"memory": {"total_gb": 32}, "disk": {"free_gb": 500}, "hardware_class": "large"}
ONLY = "qwen2.5:7b-instruct"
HERE = ["gpt-oss:20b", "qwen3:30b-a3b", ONLY]


@pytest.fixture
def only(monkeypatch):
    monkeypatch.setenv(ollama.ONLY_MODEL_ENV, ONLY)


def test_unset_changes_nothing(monkeypatch):
    monkeypatch.delenv(ollama.ONLY_MODEL_ENV, raising=False)
    got = models.fit(CENSUS, HERE)
    assert len(got["options"]) > 1


def test_the_one_model_is_the_whole_menu_and_the_default(only):
    got = models.fit(CENSUS, HERE)
    assert [o["id"] for o in got["options"]] == [ONLY]
    assert got["options"][0]["recommended"] and got["options"][0]["installed"]
    assert got["recommended"]["id"] == ONLY and [o["id"] for o in got["already_here"]] == [ONLY]


def test_a_model_not_here_is_still_the_only_row_and_says_it_is_not_here(only):
    got = models.fit(CENSUS, ["gpt-oss:20b"])
    assert [o["id"] for o in got["options"]] == [ONLY] and not got["options"][0]["installed"]


def test_no_pull_reaches_the_server(only, monkeypatch):
    rt = ollama.OllamaRuntime("http://127.0.0.1:9")
    monkeypatch.setattr(rt, "_request", lambda *a, **k: pytest.fail("it called the model server"))
    assert rt.pull("anything") is False and rt.pull(ONLY) is False
    assert "downloads no models" in rt._pulls["anything"]["error"]


def test_no_other_model_loads_or_answers(only, monkeypatch):
    rt = ollama.OllamaRuntime("http://127.0.0.1:9")
    monkeypatch.setattr(rt, "_request", lambda *a, **k: pytest.fail("it called the model server"))
    with pytest.raises(BrainError) as err:
        rt.chat([{"role": "user", "content": "hi"}], model="gpt-oss:20b")
    assert err.value.code == "only_model" and ONLY in str(err.value)
    rt.warm("gpt-oss:20b")
    assert "gpt-oss:20b" not in rt._loading


def test_every_chat_model_on_this_ollama_is_a_choice(monkeypatch):
    monkeypatch.delenv(ollama.ONLY_MODEL_ENV, raising=False)
    got = models.fit(CENSUS, HERE + ["dolphin-llama3:8b", "nomic-embed-text:latest", "someone/thing:cloud"])
    ids = [o["id"] for o in got["options"]]
    assert "dolphin-llama3:8b" in ids and next(o for o in got["options"] if o["id"] == "dolphin-llama3:8b")["installed"]
    assert "nomic-embed-text:latest" not in ids and "someone/thing:cloud" not in ids
    assert len([o for o in got["options"] if o.get("recommended")]) == 1
