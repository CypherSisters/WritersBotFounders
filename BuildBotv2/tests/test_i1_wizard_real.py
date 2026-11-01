"""Integration lane I1: ``puffpai setup`` (the terminal renderer) on the Core's REAL parts: lane L3's vault,
lane L4's Bridge posture, lane L2's model fit and its one model setting, and (since lane I2a) lane L6's
Toolsmith: the goal becomes jobs on the local brain's fallback. The plan is recorded as "no plan this time"
here (no GitHub in a unit test), so setup ends not complete. Setup starts the Core on free ports and stops
it (``--exit``). Scripted answers; no real DPAPI (conftest's fake machine key), no model download."""

import json

from puffpai_core import wiring
from puffpai_core.wizard import terminal as term_mod

PIN = "Tq7#mz2Lp!"                     # a made-up test PIN (z1kit's)
CENSUS = {"schema": "hardware_census/v1", "os": {"system": "Windows"}, "memory": {"total_gb": 31.7},
          "cpu": {"model": "Test CPU", "logical_cores": 8}, "accelerator": {"kind": "unknown"},
          "soc": {"model": None, "is_jetson": False}, "disk": {"total_gb": 900, "free_gb": 410},
          "model_budget_gb": 25.4, "notes": [], "programs": {"docker": False}}


def test_puffpai_setup_runs_the_vault_brain_and_bridge_steps_for_real(state, tmp_path, monkeypatch, capsys):
    from puffpai_core.wizard import census as census_mod
    from puffpai_core.zone1 import archivist as arch_mod
    from puffpai_core.zone2 import settings as model_settings

    model_settings.save(state, address="http://127.0.0.1:9")       # nothing listens: no real model server
    monkeypatch.setattr(census_mod, "run_census", lambda *a, **k: dict(CENSUS))
    pulled = []
    monkeypatch.setattr(wiring.WizardModels, "pull", lambda self, name: pulled.append(name))
    codes = []
    real_setup = arch_mod.Archivist.setup
    monkeypatch.setattr(arch_mod.Archivist, "setup", lambda self, pin: codes.append(real_setup(self, pin)) or codes[-1])
    data = tmp_path / "PuffPAI Data"
    picked = {}
    hidden = []

    def read(self, field, prompt):
        fid = field["id"]
        if field.get("hidden"):
            hidden.append(fid)
        if fid == "model":
            picked["model"] = field["options"][0]["value"]
            return "1"
        answers = {"where": "1", "pin": PIN, "pin_again": PIN, "data_folder": str(data), "linked": "", "posture": "1",
                   "goal": "a world news map", "yes": "no"}
        if fid in ("recovery", "recovery_confirm"):
            return codes[-1]
        return answers.get(fid, "")
    monkeypatch.setattr(term_mod.Terminal, "read", read)
    monkeypatch.setattr(term_mod, "builtins", lambda core, ask, write: [])       # tested on its own (lane I2a)
    monkeypatch.setattr(wiring.WizardToolsmith, "plan",
                        lambda self, build, project=None: {"summary": None, "items": ["No plan this time."]})
    from i1_e2e import free_port
    argv = ["--port", str(free_port()), "--pages-port", str(free_port()), "--proxy-port", "0", "--exit"]

    assert term_mod.main(argv) == 1                   # not complete: no plan came, so there is nothing to say Yes to
    out = capsys.readouterr().out

    # Lane L3: a real vault, set up and open, with a recovery code; the PIN was read hidden.
    a = arch_mod.for_state(state)
    assert a.vault.is_set_up() and a.vault.is_open() and codes and a.vault.test_recovery(codes[-1])
    assert "pin" in hidden and PIN not in out
    assert seams_verify(state, PIN) is True
    # Lane L2: the pick is the brain's one model setting.
    assert model_settings.load(state).model == picked["model"]
    assert json.loads((state / "settings" / "model.json").read_text(encoding="utf-8"))["address"] == "http://127.0.0.1:9"
    assert pulled == [picked["model"]]
    # Lane L4: "search" opened the Bridge with Toolsmith's search switch on.
    from puffpai_core.zone3.service import current
    posture = current().bridge.posture()
    assert posture["state"] == "open" and posture["switches"].get("toolsmith:search") is True
    assert data.is_dir()
    # Lane L6 (since I2a): the goal became jobs; with no plan the plan step waits.
    prog = json.loads((state / "settings" / "wizard.json").read_text(encoding="utf-8"))["steps"]
    assert {k: v["state"] for k, v in prog.items() if v["state"] == "done"}.keys() >= {"scan", "brain", "vault",
                                                                                         "bridge", "toolsmith", "goal"}
    assert prog["plan"]["state"] == "waiting" and "Job 1: " in out
    assert "not built yet" not in out and all(v["state"] != "not_built" for v in prog.values())


def seams_verify(state, pin):
    from puffpai_core import seams
    return seams.check_pin(state, pin) is None


def test_closed_posture_closes_the_bridge(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    core = wire_core(create_app(state, port=18419))             # the joins only (start_core is serve's)
    core.bridge.set_posture("search")
    core.bridge.set_posture("closed")
    posture = core.zone3.bridge.posture()
    assert posture["state"] == "closed" and posture["switches"].get("toolsmith:search") is False
