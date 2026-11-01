"""The Core's side of Toolsmith (lane L6). It runs in the Core process; Toolsmith runs in its own.

09 section 3: Toolsmith runs as its own process and reaches the Core only
through Toolsmith-token routes; the Core runs its commands, calls the checker
(Drawbridge) itself, adds the provider key at send time, and keeps the build
files. This package is that Core side:

| Module | What |
|---|---|
| ``service`` | ``ToolsmithHost``: the owner's Toolsmith routes and the handlers behind each Toolsmith-token route |
| ``process`` | starts and stops Toolsmith's process in a job object, its token on stdin |
| ``trials`` | one try: download, Drawbridge check, zone-checked commands and files ("ask" is a pause), the Docker wall, junction-safe cleanup, add bound to the checked tree hash |
| ``broker`` | the exec broker: every command and server in its own Windows job object (Steward's ``steward.jobs``), under L4's ``HookGuard``; the download (``git clone``) |
| ``sender`` + ``wire`` + ``providers/`` + ``data/known_hosts.json`` | the frontier model slot ``zone3.toolsmith-model``: screen, mapping, final-body screen, the Core's price table, the cap, the key added at send |
| ``store`` | ``builds\\<id>\\build.json``, the plan sha and the ONE Yes card, job status |
| ``settings`` | ``settings\\toolsmith.json``: the owner's model choice, budgets and timing |
| ``ports`` | every call to another lane, in one adapter (missing = the floor's answer) |
"""

from .service import ToolsmithHost

__all__ = ["ToolsmithHost"]
