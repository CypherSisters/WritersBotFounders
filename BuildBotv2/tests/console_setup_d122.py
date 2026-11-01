"""``puffpai setup`` for the D122 tests: the real Core, the real Terminal and the real shutdown route, in a real console.

The Core's guards are ``console_core``'s. Two more parts, each by an environment variable:

- ``D122_SCRIPT``: a JSON file that stands in for Toolsmith's process (``WizardToolsmith``): the plan, the jobs, a
  card, whether the build has ended. A test changes the file; nothing here needs a brain or a paid call.
- ``D122_NO_PARTS=1``: the system parts count as offered, so setup goes straight to its steps.

``--owner SPEC OUT`` runs inside the same console (``console_kit``'s *during*): it waits for each text on the screen,
types its answer, then runs ``puffpai shutdown`` (or the given ``Stop BuildBot.cmd``) and writes down what that said.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _script() -> dict:
    return json.loads(Path(os.environ["D122_SCRIPT"]).read_text(encoding="utf-8"))


def _write(data: dict) -> None:
    path = Path(os.environ["D122_SCRIPT"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _fake_toolsmith() -> None:
    from puffpai_core.wiring import WizardToolsmith as W

    def yes(self, build, use=None):
        _write({**_script(), "yes": True})
        return {"ok": True}

    def answer_card(self, build, consent_id, yes, picks=None):
        _write({**_script(), "cards": [], "answered": consent_id})
        return ""

    W.plan = lambda self, build, project=None: _script().get("plan") or {}
    W.plan_start = lambda self, build: {"ready": True}
    W.offers = lambda self, build: []
    W.yes = yes
    W.start = lambda self, build: {"ok": True, "already": True}
    W.progress = lambda self, build: {k: _script().get(k) or d for k, d in
                                      (("jobs", []), ("steps", []), ("cards", []), ("finished", False))}
    W.answer_card = answer_card
    W.answer_step = lambda self, build, step_id, answer: ""
    W.results = lambda self, build: _script().get("results") or []
    W.done_lines = lambda self: []


def _owner(spec_path: str, out_path: str) -> None:
    import console_kit
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    end = time.monotonic() + spec.get("max_s", 90)
    seen = []
    for want, keys in spec["script"]:
        while time.monotonic() < end and want not in console_kit._screen():    # noqa: SLF001
            time.sleep(0.2)
        seen.append([want, want in console_kit._screen()])                     # noqa: SLF001
        if keys is not None:
            time.sleep(0.4)
            console_kit._type(keys + "\r")                                      # noqa: SLF001
    time.sleep(spec.get("settle_s", 1.0))
    got = {"seen": seen}
    if spec.get("stop", True):
        argv = spec.get("stop_argv") or [sys.executable, str(Path(__file__).with_name("console_core.py")), "shutdown"]
        ran = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120)
        got.update(code=ran.returncode, out=ran.stdout, err=ran.stderr)
    Path(out_path).write_text(json.dumps(got), encoding="utf-8")


if __name__ == "__main__":
    if sys.argv[1] == "--owner":
        _owner(sys.argv[2], sys.argv[3])
        sys.exit(0)
    import console_core
    if os.environ.get("D122_SCRIPT"):
        _fake_toolsmith()
    if os.environ.get("D122_NO_PARTS"):
        from puffpai_core.wizard import terminal
        terminal.BUILTINS = ()
    sys.exit(console_core.main())
