r"""Toolsmith's own process, started and stopped by the Core (09 section 3, point 1).

One process per plan or build: ``python -I -m puffpai_core.zone3.toolsmith``,
inside its own Windows job object (Steward's ``steward.jobs.Job``, lane L1), with
no console window. Its Toolsmith token arrives as one JSON line on **stdin**
(never in a file, its command line or its environment), and opens only the
Toolsmith routes, for that one build (``api.auth``). Its environment has no
``PUFFPAI_*`` names and no proxy variables: the only address it talks to is
the Core API on 127.0.0.1. What it prints goes to
``logs\toolsmith\agent-<build>.log``.

When it ends (done, crashed or stopped), the Core revokes its token, ends
every try it left open (their programs by job object, their folders removed
junction-safe) and holds any job it left mid-way with a plain line
(``service.ToolsmithHost._child_done``).

Started by this launcher, not by Steward itself: Steward starts installed
packages from their Registry row and manifest, gives each a package token
and the proxy variables, and closes stdin. Toolsmith is a Core agent, not a
package (09 section 3): it must get a Toolsmith token on stdin and nothing a
package gets. The job object is Steward's own code.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from .broker import base_env, end_job, spawn

MODULE = "puffpai_core.zone3.toolsmith"
_DROP = frozenset({"http_proxy", "https_proxy", "all_proxy", "no_proxy", "npm_config_proxy",
                   "npm_config_https_proxy", "node_use_env_proxy"})


def child_env() -> dict:
    env = {k: v for k, v in base_env().items() if k.lower() not in _DROP}
    env.update(NO_PROXY="127.0.0.1,localhost,::1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    return env


class Child:
    """One running Toolsmith process."""

    def __init__(self, job: Any, build_id: str, do: str) -> None:
        self.job, self.build_id, self.do = job, build_id, do
        self.done = threading.Event()
        self.code: int | None = None

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)

    def ended(self) -> bool:
        """Has the process itself ended (D153), even while its cleanup is still running or stuck? ``done`` comes only
        after that. The watcher's ``proc.wait()`` sets ``returncode`` the moment it ends, so this is one plain read: no
        system call, no lock, and it never reaps anything, on any platform."""
        return self.done.is_set() or self.job.proc.returncode is not None

    def stop(self) -> None:
        if self.job.contained or self.job.proc.poll() is None:
            self.job.terminate(wait_s=10)


class ProcessRunner:
    """Starts Toolsmith's process for one build. ``api`` is the Core API address it calls."""

    def __init__(self, api: str | Callable[[], str], log_dir: Path | str) -> None:
        self.api = api
        self.log_dir = Path(log_dir)

    def launch(self, do: str, build_id: str, token: str, *, cache: Path,
               on_exit: Callable[[int | None], None]) -> Child:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        cache = Path(cache)
        cache.mkdir(parents=True, exist_ok=True)
        api = self.api() if callable(self.api) else self.api
        job = spawn([sys.executable, "-I", "-m", MODULE], cache, child_env(),
                    self.log_dir / f"agent-{build_id}.log", stdin=subprocess.PIPE)
        if job is None:
            raise RuntimeError("Windows could not hold Toolsmith's process in a job of its own.")
        child = Child(job, build_id, do)
        msg = json.dumps({"token": token, "api": api, "do": do, "build": build_id, "cache": str(cache)})
        try:
            job.proc.stdin.write((msg + "\n").encode("utf-8"))
            job.proc.stdin.close()
        except OSError:
            pass
        msg = token = ""                       # nothing here keeps the token

        def watch() -> None:
            code = None
            try:
                code = job.proc.wait()
            finally:
                end_job(job)                   # anything it left behind ends with it
                child.code = code
                try:
                    on_exit(code)
                finally:
                    child.done.set()
        threading.Thread(target=watch, daemon=True, name=f"toolsmith-{build_id}").start()
        return child


__all__ = ["Child", "MODULE", "ProcessRunner", "child_env"]
