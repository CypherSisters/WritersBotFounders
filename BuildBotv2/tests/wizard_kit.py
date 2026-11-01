"""Doubles for the lanes the wizard calls (L2 models, L3 vault, L4 Bridge, L6 Toolsmith),
and the two renderer adapters the acceptance tests run against (03 1.6):

- ``TerminalRun``: the real ``Terminal`` renderer with a recorded screen and
  scrollback; plain input echoes (as a real terminal does), hidden input does not.
- ``BrowserRun``: the Core API (``POST /v1/wizard/<step>``, Flask test client)
  plus the Desktop's own ``logic.js`` transcript, run in node, for what the page shows.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
from pathlib import Path

from conftest import REPO
from desktop_kit import DESKTOP, desktop_client   # D79: http://puffpai.localhost:8420
from puffpai_core.api import create_app
from puffpai_core.wizard import Engine, Services, Terminal
from puffpai_core.wizard.terminal import CLEAR

CODE = "QX7M-2KPA-9WTE-H4RD"
KEY = "sk-ant-api03-" + "AbCdEfGh12" * 5
NODE = shutil.which("node")
TRANSCRIPT_JS = REPO / "tests" / "js" / "transcript.js"

CENSUS = {"schema": "hardware_census/v1", "os": {"system": "Windows"}, "memory": {"total_gb": 31.7},
          "cpu": {"model": "Test CPU", "logical_cores": 8}, "accelerator": {"kind": "unknown"},
          "soc": {"model": None, "is_jetson": False}, "disk": {"total_gb": 900, "free_gb": 410},
          "model_budget_gb": 25.4, "notes": [], "programs": {"docker": False}}

CONSENT = ("When Toolsmith finds and installs apps, it sends Anthropic your goal and your bot's jobs, the names and "
           "descriptions of the apps it found, each app's README, docs and file list, the start of its setup files "
           "and other text files from the app's own download, the commands it runs with their output, and what the app's pages show. If you point it at a folder of "
           "your own, it reads that folder the same way. A try runs as a program on this computer, as you, so it can "
           "read your files, and what it shows goes to Anthropic. Keys and personal details it spots are taken out "
           "first, but it can miss some. Everything else stays on this computer.")


class FakeVault:
    def __init__(self, status="missing"):
        self._status = status
        self.secrets: dict[str, str] = {}
        self.inbox: dict[str, str] = {}
        self.code = CODE
        self.created = None

    def status(self):
        return self._status

    def check_pin(self, pin):
        return "" if len(pin) >= 6 and pin.isdigit() else "A PIN is at least 6 digits."

    def create(self, where, folder, pin):
        if len(pin) < 6 or not pin.isdigit():
            raise ValueError("A PIN is at least 6 digits.")
        self.created = {"where": where, "folder": folder}
        self.pin = pin
        self._status = "open"
        return self.code

    def open_existing(self, folder, pin):
        self._status = "open"

    def test_recovery(self, code):
        return code.strip().upper() == self.code

    def confirm_open(self, folder, pin):
        pass

    def new_code_problem(self):
        return ""

    def rotate_recovery(self, pin, may_open=False):
        if pin != self.pin or (self._status != "open" and not may_open):
            raise ValueError("No new code was made.")
        self._status = "open"
        self.code = "NEWC-0DE0-0000-0000"
        return self.code

    def put_secret(self, name, value):
        assert self._status == "open"
        self.secrets[name] = value

    def deposit(self, name, value):
        self.inbox[name] = value

    def lock(self):
        self._status = "locked"

    def unlock(self):
        self._status = "open"
        self.secrets.update(self.inbox)       # the inbox is filed at unlock (L3's drain)
        self.inbox.clear()

    def key_status(self, name):
        return "saved" if name in self.secrets else "inbox" if name in self.inbox else "missing"


class FakeModels:
    def __init__(self, state="ready"):
        self.current = {"state": state}
        self.pulled = []

    def fit(self, census):
        """L2's zone2.models.fit shape: {options, recommended, already_here, too_big, message}."""
        board = (census.get("soc") or {}).get("model") == "NVIDIA Jetson AGX Orin Developer Kit"
        ram = (census.get("memory") or {}).get("total_gb") or 0
        free = (census.get("disk") or {}).get("free_gb") or 0
        big = {"id": "qwen3:30b-a3b", "label": "Qwen3 30B", "disk_gb": 19, "installed": False, "fit": "comfortable",
               "recommended": False, "reason": None}
        small = {"id": "qwen3.5:9b", "label": "Qwen3.5 9B", "disk_gb": 6.6, "installed": False, "fit": "comfortable",
                 "recommended": False, "reason": None}
        if board and ram >= 48 and free >= 19 * 1.2:      # the 03 1.5 rule, as L2 answers it
            big.update(recommended=True, reason="Recommended: Qwen3 30B (19 GB download). It is the brain we "
                                                "tested on the Jetson AGX Orin 64GB, and it fits your 61 GB.")
            options, pick = [big, small], big
        else:
            small.update(recommended=True, reason="It fits your memory and uses tools well.")
            options, pick = [small], small
        return {"options": options, "too_big": [], "already_here": [], "message": None,
                "recommended": {"id": pick["id"], "label": pick["label"], "reason": pick["reason"]}}

    def menu(self, census):
        return self.fit(census)["options"]

    def state(self, name):
        return dict(self.current)

    def pull(self, name):
        self.pulled.append(name)


class FakeBridge:
    def __init__(self):
        self.posture = None

    def set_posture(self, posture):
        self.posture = posture

    def said(self):
        from puffpai_core.zone3.bridge import plain
        from puffpai_core.zone3.policy import SEARCH_HOSTS
        if self.posture != "search":
            return plain({"state": "closed", "switches": {"toolsmith:search": False}})["lines"]
        return plain({"setting": "open", "search": True, "live": [{"subject": "toolsmith:search", "from": "yes"}],
                      "search_hosts": list(SEARCH_HOSTS)})["lines"]


class FakeToolsmith:
    def __init__(self, vault=None, providers=True):
        self.vault = vault
        self._providers = providers
        self.asked = []
        self.key_at_yes = None
        self.started = False

    def providers(self):
        if not self._providers:
            return []
        return [{"id": "anthropic", "company": "Anthropic", "default_model": "opus-5.5",
                 "models": [{"id": "opus-5.5", "name": "Opus 5.5", "prices": {"in": 4, "out": 20}}],
                 "consent": CONSENT, "cost_cap_usd": 5}]

    def interpret(self, goal, use_brain):
        self.asked.append((goal, use_brain))
        return {"build": "b1", "jobs": [{"name": "World news map", "ask": "What are the top world news events?"}]}

    def plan(self, build, project=None):
        return {"summary": "Toolsmith adds a world news map: it tries World Monitor first.",
                "items": ["World news map: World Monitor (koala73/worldmonitor), needs Docker Desktop"]}

    def yes(self, build):
        if self.vault is not None:
            self.key_at_yes = self.vault.key_status("toolsmith.anthropic")
        return {"ok": True}

    def start(self, build):
        self.started = True
        return {"ok": True}

    def results(self, build):
        return [{"name": "World news map", "state": "working"}]


class FakePackages:
    """The Package Manager the build step adds ticked store apps through (CorePackages on a test Core)."""


class Kit:
    """One bot folder, the doubles, and an engine for it."""

    def __init__(self, state: Path, platform=None, env=None, vault="missing", model="ready", providers=True):
        self.state = Path(state)
        self.vault = FakeVault(vault)
        self.models = FakeModels(model)
        self.bridge = FakeBridge()
        self.toolsmith = FakeToolsmith(self.vault, providers)
        self.packages = FakePackages()        # adds ticked store apps (CorePackages on a test Core)
        self.brain = None                     # the name and look steps' JSON ask: (system, user) -> JSON
        self.census = dict(CENSUS)
        self.platform = platform
        self.env = env if env is not None else {"USERPROFILE": str(self.state / "home"), "HOME": str(self.state / "home")}

    def services(self):
        return Services(vault=self.vault, models=self.models, bridge=self.bridge, toolsmith=self.toolsmith,
                        packages=self.packages, brain=self.brain)

    def engine(self, **kw):
        return Engine(self.state, self.services(), platform=self.platform, env=self.env,
                      census_fn=lambda: self.census, brain_wait_s=0, sleep=lambda s: None, **kw)


class TerminalRun:
    name = "terminal"

    def __init__(self, kit: Kit, env=None, vt=True):
        self.kit = kit
        self.out = io.StringIO()
        self.inputs: dict[str, list] = {}
        self.ran: list[list[str]] = []
        self.hidden_reads: list[str] = []
        self.reads = 0
        self.engine = kit.engine()
        run = self

        class Recorded(Terminal):
            def read(self, field, prompt):
                run.reads += 1
                assert run.reads < 60, f"the terminal kept asking {field['id']}"
                self.out.write(prompt)
                queue = run.inputs.get(field["id"]) or [""]
                value = str(queue.pop(0)) if queue else ""
                if not field.get("hidden"):
                    self.out.write(value)          # a real terminal echoes what is typed
                else:
                    run.hidden_reads.append(field["id"])
                self.out.write("\n")
                return value

        self.term = Recorded(self.engine, out=self.out, env=env or {},
                             run=lambda cmd, **kw: self.ran.append(cmd), sleep=lambda s: time.sleep(0.05),
                             # a recorded terminal: a console that reads escape codes, or (vt=False) a classic
                             # Windows console that would print them as junk
                             tty=lambda _out: True, vt_on=lambda _out: vt)

    def step(self, step_id, inputs=None):
        self.inputs = {k: (list(v) if isinstance(v, list) else [v]) for k, v in (inputs or {}).items()}
        return self.term.run_step(step_id)

    def all_text(self) -> str:
        return self.out.getvalue()

    def screen_text(self) -> str:
        return self.out.getvalue().split(CLEAR)[-1]


# The Desktop's own requests: DESKTOP (its origin and the X-PuffPAI header) comes from desktop_kit (D79).


class BrowserRun:
    name = "browser"

    def __init__(self, kit: Kit, env=None):
        self.kit = kit
        self.app = create_app(kit.state)
        self.engine = kit.engine()
        self.app.extensions["puffpai.wizard"] = self.engine
        core = self.app.extensions["puffpai"]
        self.client = desktop_client(self.app)
        self.client.sign_in(core.tokens.issue_device("test browser"))
        self.events: list[dict] = []
        self.screens: list[dict] = []
        self.hidden_reads: list[str] = []

    def post(self, step_id, answers):
        r = self.client.post(f"/v1/wizard/{step_id}", json={"answers": answers, "renderer": "browser"},
                             headers=DESKTOP)        # what desktop.js sends with the device cookie (D49)
        assert r.status_code == 200, r.json
        self.screens.append(r.json)
        self.events.append({"screen": r.json})
        return r.json

    #: How long a step may stay "working" (the build goes on by itself) before the test fails, and how many
    #: questions one step may ask. Waiting is on the real condition (the step finishes or asks), not a count.
    WORK_WAIT_S = 180.0
    MAX_ASKS = 60

    def step(self, step_id, inputs=None):
        queues = {k: (list(v) if isinstance(v, list) else [v]) for k, v in (inputs or {}).items()}
        screen = self.post(step_id, {})
        deadline = time.monotonic() + self.WORK_WAIT_S
        asks = 0
        while True:
            if screen["status"] == "working":          # desktop.js asks again after poll_s
                if time.monotonic() > deadline:
                    raise AssertionError(
                        f"step {step_id} was still working after {self.WORK_WAIT_S:.0f} s: the test waited for it to "
                        f"finish or ask a question. Last lines: {screen.get('lines')}")
                time.sleep(0.05)
                screen = self.post(step_id, {})
                continue
            if not (screen["status"] in ("ask", "show") and screen.get("field")):
                break
            asks += 1
            if asks > self.MAX_ASKS:
                raise AssertionError(f"step {step_id}: the page kept asking {screen['field']['id']}")
            field = screen["field"]
            queue = queues.get(field["id"]) or []
            value = queue.pop(0) if queue else (field.get("default") if field.get("default") is not None else "")
            if field.get("hidden"):
                self.hidden_reads.append(field["id"])
            self.events.append({"typed": {"field": field, "value": value}})
            screen = self.post(step_id, {field["id"]: value})
        return screen

    def visible(self) -> list[str]:
        """What the page shows now, from the Desktop's own logic.js."""
        if NODE is None:
            import pytest
            pytest.skip("node is not installed, so the page's own logic can't be run")
        proc = subprocess.run([NODE, str(TRANSCRIPT_JS)], input=json.dumps(self.events), capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    def all_text(self) -> str:
        """Everything that was ever on the page: every screen and every typed echo."""
        parts = []
        for e in self.events:
            if "screen" in e:
                parts.append(json.dumps(e["screen"], ensure_ascii=False))
            elif not e["typed"]["field"].get("hidden"):
                parts.append(str(e["typed"]["value"]))
        return "\n".join(parts)

    def screen_text(self) -> str:
        return "\n".join(self.visible())


RENDERERS = [TerminalRun, BrowserRun]


def vault_inputs(where="here"):
    return {"where": where, "pin": "482913", "pin_again": "482913", "recovery_confirm": CODE, "data_folder": "",
            "linked": ""}
