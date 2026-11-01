"""Toolsmith (lane L6): find, adopt, run, prove. This package is Toolsmith's OWN process.

Toolsmith lives in the Bridge, zone 3, beside Drawbridge (D32), but in its
own process (09 section 3): the Core starts it (``toolsmith_host.process``),
and it reaches the Core only through its Toolsmith-token routes (``link``).
It imports no part of Drawbridge, the consent store or Archivist, never holds
a provider key, and starts no program (a test fails if it does). The Core's
side is ``zone3.toolsmith_host``.

| Module | What |
|---|---|
| ``__main__`` | the process: its start message (and token) on stdin |
| ``agent`` | plan a build; run a build (jobs one at a time; any error holds the job) |
| ``link`` | the Toolsmith-token routes, as methods |
| ``find`` | The owner's four questions per job: Registry reuse that fits, GitHub by stars, awesome-selfhosted, README check, one local rank, the cloud rank after the Yes |
| ``knowledge`` | the knowledge leg (lane I11): topics from one local brain ask while it plans; after her Yes, the Core's fetch stepped to its end (the Core reads, screens and keeps the text, never Toolsmith) |
| ``tries`` | the try loop: fixed tools, budgets, timing, repeat guard, "Your turn" pause, done and give_up, the model through the Core |
| ``adopt`` | the adopted ``puffpai.json`` (the working start line kept, D51) and the install through the Core |
| ``prove`` | health, the main page, ``read_app`` and the job's sample ask |
| ``commands`` | how a command line is read (reads, builds, compose ``up``); runs nothing |
| ``runbook`` + ``data/runbook.json``, ``readme_steps``, ``programs`` | the run notes, the README Quick Start, missing programs as steps |
| ``errors``, ``names`` | the errors both sides share; her account name as "you" |
"""

__all__: list[str] = []
