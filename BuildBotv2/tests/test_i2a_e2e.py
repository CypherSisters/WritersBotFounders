"""Integration lane I2a, ADOPT end to end on real parts (``-m e2e``): see ``tests/i2a_e2e.py`` for the steps.

Toolsmith's model turns are scripted here (the plumbing); the brain is this computer's local model, and
GitHub and the project's downloads are real (read-only, through the Bridge). Skipped when the local model
is not there or GitHub can't be reached. The live run with the local model driving Toolsmith is the script:
``.venv\\Scripts\\python tests\\i2a_e2e.py --state <empty folder>``.
"""

import urllib.request

import pytest

import i2a_e2e
from test_i1_e2e import _model_here

pytestmark = pytest.mark.e2e


def _github_here() -> bool:
    try:
        req = urllib.request.Request(f"https://raw.githubusercontent.com/{i2a_e2e.FULL}/HEAD/README.md",
                                     headers={"User-Agent": "PuffPAI-BuildBot/2 (+https://github.com)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except OSError:
        return False


@pytest.mark.skipif(not _model_here(), reason=f"{i2a_e2e.MODEL} is not on {i2a_e2e.ADDRESS}")
def test_adopt_a_notes_app_through_the_wizard(state, request, monkeypatch):
    if "e2e" not in (request.config.getoption("markexpr") or ""):
        pytest.skip("end-to-end run: run with -m e2e")
    if not _github_here():
        pytest.skip("GitHub can't be reached from here")
    from puffpai_core import wiring
    from puffpai_core.zone2 import brain
    monkeypatch.setattr(brain, "toolsmith_turn", brain.toolsmith_turn)         # restored after the run
    monkeypatch.setattr(wiring.WizardModels, "pull", wiring.WizardModels.pull)
    # A short bot folder: under pytest's deep temp path the app's own .venv passes Windows' 260-character
    # limit (sqlalchemy's DLL does not load). Made here, removed here.
    import shutil
    import tempfile
    from pathlib import Path
    short = Path(tempfile.mkdtemp(prefix="i2a"))
    monkeypatch.setenv("PUFFPAI_STATE", str(short))
    try:
        steps = i2a_e2e.run(short, scripted=True, log=print)
    finally:
        shutil.rmtree(short, ignore_errors=True)
    failed = [s for s in steps if not s["ok"]]
    assert not failed, "\n".join(f"{s['step']}: {s['said']}" for s in failed)
