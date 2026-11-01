# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/prove.py (rewritten-from)
"""PROVE: "working" is seen, never declared (03 Part 4, v1 ``build/prove.py``).

For an adopted app (no MCP) after Steward restarted it from its manifest:
1. its health, as Caretaker probed it (a probe that failed is "not yet"; one
   not made yet is said so, never counted as a pass);
2. its main page loads with real content, and is not a metrics, health, JSON
   or error page (run note 6);
3. the brain's ``read_app`` returns its visible text;
4. the job's sample ask goes to the brain with that text, and the answer uses
   words from the page.

The result is ``working`` or ``not_yet`` with one plain reason that names the
app. A part another lane builds that is missing gives ``not_yet`` with that
line: nothing is claimed. It runs in Toolsmith's process; every read goes
through the Core (``link``: the page reader, ``read_app``, the brain).

v1 source: ``build/prove.py`` (``prove`` :137, ``_prove_app`` :174, ``_read_answer`` :249).
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import runbook
from .errors import NotBuilt
from .tries import error_page, words


#: What Caretaker writes the moment an app starts, before its first real check (D132). It is no failed check: the page
#: read below is the proof.
STARTING = "Starting."
PAGE_TRIES, PAGE_WAIT_S = 4, 5.0


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", str(text or ""))}


def prove(link: Any, row: dict | None, pkg_id: str, name: str, job: dict, page_url: str) -> dict:
    """``row`` is the package's Registry row (or None); ``link`` reaches the Core."""
    out = {"result": "not_yet", "why": "", "read": "", "answer": "", "health": None}
    if row is not None:
        health = row.get("health") or {}
        out["health"] = health
        if health.get("checked_at") and not health.get("ok") and health.get("reason") != STARTING:
            out["why"] = f"{name} did not pass its health check: {health.get('reason') or 'no reason given'}"
            return out
    try:
        page = link.read_page(page_url)
        for _ in range(PAGE_TRIES):           # it started again just now: a slow computer needs a moment (D132)
            if page.get("ok") and words(str(page.get("text") or "")) >= 3:
                break
            time.sleep(PAGE_WAIT_S)
            page = link.read_page(page_url)
    except NotBuilt as exc:
        out["why"] = f"{name}: its page could not be read ({exc})"
        return out
    text = str(page.get("text") or "")
    if not page.get("ok") or words(text) < 3 or error_page(page):
        out["why"] = f"{name}'s page shows no real content after it started again from its puffpai.json."
        return out
    other = runbook.not_main_page(page, page_url, name)
    if other:
        out["why"] = f"{name}: that is not its main page ({other})."
        return out
    try:
        read = link.read_app(pkg_id)
        seen = str(read.get("text") or read.get("said") or "") if isinstance(read, dict) else str(read or "")
        if isinstance(read, dict) and read.get("ok") is False:
            out["why"] = f"The brain could not read {name}: {read.get('said') or 'no answer'}"
            return out
        out["read"] = seen[:2000]
        answer = link.brain_answer(job.get("sample_ask") or f"What does {name} show?", seen[:6000])
    except NotBuilt as exc:
        out["why"] = f"{name} runs, but the brain could not check what it shows yet ({exc})."
        return out
    out["answer"] = answer[:1000]
    if not answer.strip() or not (_words(answer) & _words(seen)):
        out["why"] = f"{name} runs, but the brain's answer to the sample ask did not use what its page shows."
        return out
    out["result"] = "working"
    if not (out["health"] or {}).get("checked_at"):
        out["note"] = "Caretaker has not checked its health yet."
    return out


__all__ = ["prove"]
