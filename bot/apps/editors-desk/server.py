"""Editor's Desk: a publishing house's stages of editing for a writer's book, run by "your editor" in the background.

One small server, bound to 127.0.0.1. The design is in WritersBot's EDITORS_DESK.md:
- ``/``                 the page (web/index.html), shown in the Desktop; ``/health`` "ok"; ``/mcp`` the tools
- ``/api/list``         GET: the books on the shelf
- ``/api/open``         POST ``{title}``: brings the old desk's marks over, once
- ``/api/desk``         GET ``?title=``: everything the page shows for one book
- ``/api/section``      GET ``?title=&n=``: one section's text, its sha and its earlier versions
- ``/api/version``      GET ``?title=&n=&id=``: one earlier version
- ``/api/save``         POST ``{title, n, text, sha}``: the writer's own words. The old version is kept first
- ``/api/restore``      POST ``{title, n, id, sha}``: an earlier version back, the one before it kept too
- ``/api/use``          POST ``{title, id}``: PROPOSED, Use this: a grammar or proofreading correction, on her click only
- ``/api/run``          POST ``{title, check, plan?}``: her press; ``/api/stop`` POST ``{title}``
- ``/api/mark``         POST ``{title, id, state?, note?}``
- ``/api/document``     GET ``?title=&id=``; POST ``{title, id, text}``: a document she keeps, never the manuscript
- ``/api/settings``     GET, POST ``{vault_free}``

Every route refuses a request without this package's PUFFPAI_TOKEN. GET never changes anything. No tool writes section
text, and no brain answer reaches a section: only ``/api/save``, ``/api/restore`` and ``/api/use`` do, from her hand.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))      # its own modules, also when started with python -I
import book as books  # noqa: E402
import engine  # noqa: E402
from book import SHARED, Book, Refused, find_book, locate, read_index, save_section, section_path, version_text, versions  # noqa: E402
from brainline import (BrainBusy, BrainDown, BrainQueue, BrainSlow, NotYet, Brain, now, plain, read_json, sha_of, strip_think,  # noqa: E402
                       tokens, write_atomic, write_json)
from engine import NeedsReading, Stopped  # noqa: E402

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
PAGE = WEB / "index.html"
NAME = "editors-desk"
VERSION = "0.2.1"
MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 4 * 1024 * 1024
WEB_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
             ".html": "text/html; charset=utf-8"}
SHELF = "Manuscripts"
USE_THIS = True            # PROPOSED: the owner may remove this. False takes the route away and hides the button
ASK_WAIT = float(os.environ.get("EDITORS_DESK_ASK_WAIT_S") or 50)   # a tool call stops at 60 seconds
DEFAULT_CONTEXT = 8192
# What model_profiles.json sets, for the room in an ask, until the Core's answer says it itself (PROPOSED: one line in
# package_chat). settings.json's context_tokens wins over this table.
MODEL_CONTEXT = {"qwen3:30b-a3b": 8192, "qwen3.5:9b": 16384, "qwen3-coder:30b": 16384, "qwen2.5:7b-instruct": 8192}
DEFAULTS = {"vault_free": True, "context_tokens": 0}
JOBS = "jobs.json"
TIMING = "timing.json"
SETTINGS = "settings.json"


class CoreFiles:
    """Asks the Core where My files is, each time: she may rename the bot or move her data folder while this runs."""

    def __init__(self, api: str | None, token: str | None) -> None:
        self.api = (api or "").rstrip("/")
        self.token = token or ""

    def shelf(self) -> Path:
        req = urllib.request.Request(self.api + "/v1/owner-files", headers={"Authorization": f"Bearer {self.token}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # the Core is on this computer
        try:
            with opener.open(req, timeout=10) as r:
                made = str(json.loads(r.read().decode("utf-8")).get("made") or "")
        except (OSError, ValueError, AttributeError, urllib.error.URLError):
            made = ""
        if not made:
            raise Refused("I could not ask your bot where your files are, so nothing was read or changed.")
        return Path(made).parent / SHELF


class Timing:
    """Real measured call times, kept over restarts: this is where "about 20 minutes left" comes from."""

    def __init__(self, path: Path) -> None:
        self.path = path
        got = read_json(path, {}) or {}
        self.n, self.total = int(got.get("n") or 0), float(got.get("seconds") or 0)
        self.model = str(got.get("model") or "")

    def note(self, seconds: float, model: str = "") -> None:
        self.n, self.total = self.n + 1, self.total + seconds
        self.model = model or self.model
        if self.n > 200:                 # the recent past counts most
            self.n, self.total = 100, self.total / 2
        try:
            write_json(self.path, {"n": self.n, "seconds": round(self.total, 1), "model": self.model})
        except OSError:
            pass

    def left(self, calls: int) -> int | None:
        return int(self.total / self.n * max(0, calls)) if self.n >= 2 else None


def _when_left(seconds: Any) -> str:
    if not isinstance(seconds, (int, float)):
        return ""
    if seconds < 90:
        return "about a minute left"
    if seconds < 3600:
        return f"about {round(seconds / 60)} minutes left"
    return f"about {seconds / 3600:.1f} hours left"


class Desk:
    def __init__(self, files: Any, brain: Brain | None = None) -> None:
        self.files = files
        self.brain = brain or Brain("", "")
        self.data = engine.data_root()
        self.data.mkdir(parents=True, exist_ok=True)
        self.brain.as_written = self.vault_free
        self.queue = BrainQueue()
        self.timing = Timing(self.data / TIMING)
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop_flag: set[str] = set()
        threading.Thread(target=self._worker, daemon=True, name="your editor").start()

    # settings

    def settings(self) -> dict:
        got = read_json(self.data / SETTINGS, {})
        got = got if isinstance(got, dict) else {}
        return {k: (got[k] if isinstance(got.get(k), type(v)) else v) for k, v in DEFAULTS.items()}

    def vault_free(self) -> bool:
        return bool(self.settings()["vault_free"])

    def settings_save(self, msg: Any) -> dict:
        if not isinstance(msg, dict) or not isinstance(msg.get("vault_free"), bool):
            raise Refused("Say vault_free: true or false.")
        got = {**self.settings(), "vault_free": msg["vault_free"]}
        write_json(self.data / SETTINGS, got)
        return {"settings": got, "message": "Manuscripts stay out of the vault." if got["vault_free"] else "Manuscripts go through the vault."}

    def context(self) -> int:
        """How many tokens the brain has: what its answer says (PROPOSED), the setting, the table by model, else 8,192."""
        told = getattr(self.brain, "last", {}).get("context")
        if isinstance(told, int) and told > 0:
            return told
        mine = int(self.settings().get("context_tokens") or 0)
        if mine > 0:
            return mine
        model = str(getattr(self.brain, "last", {}).get("model") or self.timing.model or "")
        return MODEL_CONTEXT.get(model, DEFAULT_CONTEXT)

    def room(self) -> int:
        return int(self.context() * engine.ROOM_SHARE)

    # books

    def shelf(self) -> Path:
        return self.files.shelf()

    def _folder(self, title: Any) -> Path:
        return find_book(self.shelf(), title)

    def list_view(self) -> dict:
        out = []
        for f in books.all_books(self.shelf()):
            idx = read_index(f)
            out.append({"title": idx.get("title") or f.name, "sections": len(idx.get("sections") or []),
                        "words": sum(int(c.get("words") or 0) for c in idx.get("sections") or []),
                        "read": (f / books.BIBLE).exists()})
        return {"books": out}

    def open_book(self, title: Any) -> dict:
        folder = self._folder(title)
        return {"carried": engine.carry_old_desk(folder)}

    # the line of pressed checks

    def _jobs(self) -> list[dict]:
        got = read_json(self.data / JOBS, [])
        return [j for j in got if isinstance(j, dict)] if isinstance(got, list) else []

    def _save_jobs(self, jobs: list[dict]) -> None:
        write_json(self.data / JOBS, jobs)

    def _update(self, job: dict) -> None:
        with self.lock:
            jobs = self._jobs()
            for i, j in enumerate(jobs):
                if j.get("id") == job.get("id"):
                    jobs[i] = job
            self._save_jobs(jobs)

    def start(self, title: Any, check: Any, plan: Any = "") -> tuple[dict, dict]:
        """Her press, or her Yes through a tool. The only way a check ever starts."""
        checks = engine.load_checks()
        c = checks.get(str(check or "").strip().lower())
        if c is None:
            raise Refused("There is no check by that name. The checks are: " + ", ".join(checks) + ".")
        folder = self._folder(title)
        engine.carry_old_desk(folder)
        words = chr(10).join(plain(x) for x in str(plan or "").splitlines()).strip()[:1000]
        if c["box_needed"] and not words:
            raise Refused(f"Say what goes in the box first: {c['box']}")
        b = Book(folder)
        if c["needs_bible"] and not b.bible:
            raise Refused(f"{c['name']} goes by the story bible, and {b.title} has not been read yet. Read it in Manuscript first.")
        with self.lock:
            jobs = self._jobs()
            if any(j.get("folder") == folder.name and j.get("check") == c["id"] for j in jobs):
                raise Refused(f"{c['name']} is already in line for {b.title}.")
            job = {"id": f"{int(time.time() * 1000)}-{c['id']}", "folder": folder.name, "title": b.title, "check": c["id"], "check_name": c["name"],
                   "plan": words, "state": "queued", "step": "In line", "done": 0, "total": 0, "found": 0, "problems": [], "started_at": None, "pressed_at": now()}
            jobs.append(job)
            self._save_jobs(jobs)
        self.wake.set()
        return job, c

    def stop(self, title: Any) -> str:
        folder = self._folder(title)
        with self.lock:
            jobs = self._jobs()
            mine = [j for j in jobs if j.get("folder") == folder.name]
            if not mine:
                return "Nothing is running for this book."
            waiting = [j for j in mine if j.get("state") == "queued"]
            self._save_jobs([j for j in jobs if j not in waiting])
            for j in mine:
                if j not in waiting:
                    self.stop_flag.add(j["id"])
        return "Stopping after the call in progress. Press the check again and it goes on from where it was."

    def _book_status(self, job: dict) -> None:
        """Kept with the app's files, and as one small file beside the bible, so a sibling app can see what the brain is
        doing, in Manuscript's shape."""
        try:
            folder = self.shelf() / job["folder"]
            if folder.is_dir():
                st = {k: job.get(k) for k in ("state", "step", "done", "total", "started_at", "finished_at", "check", "check_name", "found", "left_s", "problems")}
                write_json(engine.home_of(folder) / "status.json", st)
                write_json(folder / SHARED, {k: st[k] for k in ("state", "step", "done", "total", "check_name")})
        except (OSError, Refused):
            pass

    def _worker(self) -> None:
        """One check at a time, in the order she pressed them. A check that was running when the app stopped goes on."""
        tidied = False
        while True:
            jobs = self._jobs()
            if not tidied:
                try:
                    engine.tidy(self.shelf())
                    tidied = True
                except (Refused, OSError):
                    pass
            if not jobs:
                self.wake.wait(5)
                self.wake.clear()
                continue
            job = jobs[0]
            try:
                shelf = self.shelf()
            except Refused:
                time.sleep(5)                    # the Core is not up yet
                continue
            self._run(job, shelf)

    def _run(self, job: dict, shelf: Path) -> None:
        folder = shelf / job["folder"]
        checks = engine.load_checks()
        c = checks.get(job.get("check"))
        if c is None or not folder.is_dir():
            self._finish(job, "failed", "That check or book is no longer there.")
            return
        resumed = job.get("state") in ("running", "waiting")
        job.update(state="running", started_at=job.get("started_at") or now(), resumed=resumed, done=0, total=0)

        def put(**kw: Any) -> None:
            job.update(kw)
            self._update(job)
            self._book_status(job)

        run = _Run(folder, c, job, self.brain, self.queue, self.room, self.timing, put, lambda: job["id"] in self.stop_flag, self.shelf)
        put()
        try:
            got = run.run()
        except Stopped:
            self.stop_flag.discard(job["id"])
            self._finish(job, "stopped", "Stopped. Press it again and it goes on from where it was.")
            return
        except NeedsReading:
            self._finish(job, "failed", f"{c['name']} goes by the story bible, and this book has not been read yet.")
            return
        except (BrainDown, Refused) as exc:
            self._finish(job, "failed", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - never out of the thread; a plain line in the status
            traceback.print_exc()
            self._finish(job, "failed", f"This check stopped on an error ({type(exc).__name__}). Press it again and it goes on from where it was.")
            return
        ran = read_json(engine.home_of(folder) / engine.RUNS, {}) or {}
        ran[c["id"]] = {"finished_at": now(), "plan": job.get("plan") or "", **got}
        write_json(engine.home_of(folder) / engine.RUNS, ran)
        line = "Your editor's letter is written." if c["id"] == "letter" else f"{c['name']} is done. " + (f"Found {got['found']} worth your eye." if got["found"] else "Nothing to report.") if not c["makes"] and not any(b.startswith("write ") for b in c["blocks"]) else f"{c['name']} is done. See Documents."
        if not got.get("found") and not c["makes"] and c["id"] != "letter" and not any(b.startswith("write ") for b in c["blocks"]):
            line = f"Nothing big found on {c['name'].lower()}." + (f" Here is what I checked: {got['checked']}." if got.get("checked") else "")
        if got.get("skipped"):
            steps = "; ".join(got.get("skipped_steps") or [])
            line += f" Not finished: your bot ran out of time on {steps}, even with less text. Press it again to try that part; nothing you have is lost."
        self._finish(job, "done", line, problems=got.get("problems") or [], found=got.get("found", 0))

    def _finish(self, job: dict, state: str, line: str, **more: Any) -> None:
        job.update(state=state, step=line, finished_at=now(), left_s=None, **more)
        self._book_status(job)
        with self.lock:
            self._save_jobs([j for j in self._jobs() if j.get("id") != job.get("id")])

    def status_of(self, folder: Path) -> dict | None:
        """What is happening for this book now, or its last word."""
        jobs = [j for j in self._jobs() if j.get("folder") == folder.name]
        now_ = next((j for j in jobs if j.get("state") in ("running", "waiting")), None) or (jobs[0] if jobs else None)
        if now_:
            return {**now_, "left": _when_left(now_.get("left_s")), "in_line": [j["check_name"] for j in jobs if j is not now_]}
        last = read_json(engine.home_of(folder) / "status.json")
        return last if isinstance(last, dict) else None

    # what the page shows

    def desk_view(self, title: str) -> dict:
        folder = self._folder(title)
        status = self.status_of(folder)              # first: "done" must never come with findings read before it was
        b = Book(folder)
        checks = engine.load_checks()
        items = engine.load_findings(folder)
        ran = read_json(engine.home_of(folder) / engine.RUNS, {}) or {}
        shas = {s.n: s.sha for s in b.sections}
        out = []
        items = [f for f in items if not engine.PRIVATE.search(engine.json_text(f))]   # never a finding about a vault handle
        for f in items:
            c = checks.get(f.get("check")) or {}
            changed = any(shas.get(int(n)) != v for n, v in (f.get("sha") or {}).items() if str(n).isdigit())
            out.append({**f, "check_name": c.get("name") or "", "stage": c.get("stage") or "any", "changed": changed,
                        "can_use": bool(USE_THIS and f.get("correct") and c.get("use_this"))})
        out.sort(key=lambda f: (engine.SIZE_RANK.get(f.get("size"), 1), int(f.get("rank") or 0)))
        job_rows = {j.get("check"): j for j in self._jobs() if j.get("folder") == folder.name}
        rows = {}
        for cid, c in checks.items():
            r = ran.get(cid) or {}
            mine = [f for f in items if f.get("check") == cid]
            rows[cid] = {k: c[k] for k in ("id", "name", "says", "trust", "thin", "box", "box_needed", "button", "stage", "needs_bible")}
            rows[cid].update(last_run=r.get("finished_at"), found=r.get("found"), skipped=r.get("skipped_steps") or [],
                             open=sum(1 for f in mine if f.get("state") == "open" and f.get("kind") != "question"),
                             fixed=sum(1 for f in mine if f.get("state") == "fixed"), running=(job_rows.get(cid) or {}).get("state"),
                             can_run=not c["needs_bible"] or bool(b.bible))
        stages = []
        for s in engine.STAGES:
            mine = [rows[c] for c in rows if rows[c]["stage"] == s["id"]]
            big = sum(1 for f in items if f.get("state") == "open" and f.get("size") == "big" and f.get("kind") != "question"
                      and (checks.get(f.get("check")) or {}).get("stage") == s["id"])
            stages.append({**s, "checks": mine, "big_open": big, "ran": sum(1 for x in mine if x["last_run"]), "total": len(mine)})
        docs = engine.load_documents(folder)
        reading = read_json(folder / books.STATUS, {}) or {}
        return {"title": b.title, "version": VERSION, "has_bible": bool(b.bible), "built_with": b.bible.get("built_with"),
                "sections": [{"n": s.n, "heading": s.heading, "words": s.words} for s in b.sections], "unread": b.unread(),
                "words": sum(s.words for s in b.sections), "reading": reading.get("state") == "reading", "status": status,
                "letter": read_json(engine.home_of(folder) / engine.LETTER), "plan": engine.revision_plan(items, checks), "open_now": sum(1 for f in items if f.get("state") == "open" and f.get("kind") != "question"), "private": b.private,
                "stages": stages, "anytime": [rows[c] for c in rows if rows[c]["stage"] == "any" and c != "letter"],
                "old_notes": self._old_notes(folder), "next": self._next(stages), "findings": out, "documents": docs, "use_this": USE_THIS, "context": self.context()}

    @staticmethod
    def _old_notes(folder: Path) -> str:
        """Her notes from the old Editor's desk tab, shown as they are so nothing is lost. Manuscript keeps the file."""
        for name in ("editing.md", "desk.md"):
            try:
                text = (folder / name).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            lines = text.splitlines()
            return "\n".join(lines[1:]).strip() if lines and lines[0].startswith("# ") else text
        return ""

    @staticmethod
    def _next(stages: list[dict]) -> dict | None:
        for s in stages:
            todo = next((c for c in s["checks"] if not c["last_run"] and not c["running"]), None)
            if todo:
                return {"check": todo["id"], "name": todo["name"], "stage": s["name"], "why": s["why"], "box": todo["box"], "box_needed": todo["box_needed"], "can_run": todo["can_run"],
                        "button": todo["button"], "says": todo["says"]}
            if s["big_open"]:
                n = s["big_open"]
                return {"check": None, "stage": s["name"], "why": f"{n} big thing{'s are' if n > 1 else ' is'} still open in {s['name']}. Settle {'them' if n > 1 else 'it'} before going on. Smaller work now may have to be done again."}
        return None

    def section_view(self, title: str, n: str) -> dict:
        folder = self._folder(title)
        b = Book(folder)
        s = b.section(n)
        if s is None:
            raise Refused("I could not find that section.")
        return {"n": s.n, "heading": s.heading, "text": s.text.rstrip("\n"), "sha": s.sha, "words": s.words, "unread": s.n in b.unread(),
                "versions": versions(self._vroot(folder), s.n), "numbers": [x.n for x in b.sections]}

    def version_view(self, title: str, n: str, vid: str) -> dict:
        folder = self._folder(title)
        if not str(n).isdigit():
            raise Refused("I could not find that section.")
        return {"id": vid, "text": version_text(self._vroot(folder), int(n), vid)}

    @staticmethod
    def _vroot(folder: Path) -> Path:
        return engine.home_of(folder) / engine.VERSIONS

    # the writer's hand: the only ways text changes

    def save(self, msg: dict) -> dict:
        folder = self._folder(msg.get("title"))
        with self.lock:
            got = save_section(folder, self._vroot(folder), msg.get("n"), msg.get("text"), msg.get("sha"))
        return {**got, "message": "Saved." if got["changed"] else "No change to save.", "versions": versions(self._vroot(folder), int(msg.get("n")))}

    def restore(self, msg: dict) -> dict:
        folder = self._folder(msg.get("title"))
        if not str(msg.get("n")).isdigit():
            raise Refused("I could not find that section.")
        text = version_text(self._vroot(folder), int(msg.get("n")), msg.get("id"))
        with self.lock:
            got = save_section(folder, self._vroot(folder), msg.get("n"), text, msg.get("sha"), by="restored")
        return {**got, "text": text, "message": "That version is back. The one you had is kept too.", "versions": versions(self._vroot(folder), int(msg.get("n")))}

    def use_this(self, msg: dict) -> dict:
        """PROPOSED, and easy to remove. Her click, after the page showed before and after; undone like any save."""
        if not USE_THIS:
            raise Refused("Use this is turned off.")
        folder = self._folder(msg.get("title"))
        checks = engine.load_checks()
        with self.lock:
            items = engine.load_findings(folder)
            f = next((x for x in items if x.get("id") == msg.get("id")), None)
            if f is None or not f.get("correct") or not (checks.get(f.get("check")) or {}).get("use_this"):
                raise Refused("That finding has no correction to use.")
            b = Book(folder)
            w = f["where"][0]
            s = b.section(w.get("section"))
            span = locate(s.text, w.get("quote") or "") if s else None
            if not span:
                raise Refused("Those words are not in the section any more, so nothing was changed.")
            text = s.text[:span[0]] + f["correct"] + s.text[span[1]:]
            got = save_section(folder, self._vroot(folder), s.n, text, s.sha, by="Use this")
            f.update(state="fixed", changed_at=now(), note=(f.get("note") or "") or "Used the correction.")
            engine.save_findings(folder, items)
        return {**got, "message": "Done, and saved. You can bring back the old version under Earlier versions.", "n": s.n}

    def mark(self, title: Any, ref: Any, state: Any = None, note: Any = None) -> tuple[str, dict]:
        folder = self._folder(title)
        engine.carry_old_desk(folder)
        with self.lock:
            items = engine.load_findings(folder)
            f = self._pick(items, ref)
            words = {"fixed": "Fixed", "not_a_problem": "Not a problem", "later": "Later", "open": "Open"}
            st = None if state in (None, "") else str(state).strip().lower().replace(" ", "_")
            if st is not None and st not in engine.STATES:
                raise Refused("A finding can be Open, Fixed, Not a problem or Later.")
            if st:
                f["state"] = st
                f.pop("still_broken", None)
            if note is not None:
                f["note"] = " ".join(str(note).split())[:5000]
            f["changed_at"] = now()
            engine.save_findings(folder, items)
        return f"Marked {words.get(f['state'])}: {f.get('what')}", f

    @staticmethod
    def _pick(items: list[dict], ref: Any) -> dict:
        r = str(ref if ref is not None else "").strip()
        if r.isdigit():
            opens = sorted([f for f in items if f.get("state") == "open"], key=lambda f: (engine.SIZE_RANK.get(f.get("size"), 1), int(f.get("rank") or 0)))
            if not 1 <= int(r) <= len(opens):
                raise Refused(f"There are {len(opens)} open findings.")
            return opens[int(r) - 1]
        got = next((f for f in items if f.get("id") == r), None)
        if got is None:
            raise Refused("I could not find that finding. Ask for the findings again.")
        return got

    def document_view(self, title: str, did: str) -> dict:
        folder = self._folder(title)
        rec, path = self._doc(folder, did)
        out = {**rec, "where": f"My files/{engine.EXPORTS}/{folder.name}/{rec['file']}"}
        if rec.get("ext") != "docx":
            out["text"] = path.read_text(encoding="utf-8")
        return out

    @staticmethod
    def _doc(folder: Path, did: Any) -> tuple[dict, Path]:
        rec = next((d for d in engine.load_documents(folder) if d.get("id") == did), None)
        if rec is None:
            raise Refused("I could not find that document.")
        home = engine.exports_of(folder)
        path = (home / str(rec["file"])).resolve()
        if path.parent != home.resolve() or not path.is_file():
            raise Refused("I could not find that document. It may have been moved in your files.")
        return rec, path

    def document_save(self, msg: dict) -> dict:
        folder = self._folder(msg.get("title"))
        rec, path = self._doc(folder, msg.get("id"))
        if rec.get("ext") == "docx" or not isinstance(msg.get("text"), str):
            raise Refused("That document can't be changed here.")
        write_atomic(path, msg["text"])
        return {"message": "Saved."}

    # tools. None of them writes section text.

    def desk_checks(self, title: str = "") -> str:
        v = self.desk_view(title)
        lines = [f"The stages of editing for {v['title']}:"]
        for s in v["stages"]:
            lines.append(f"{s['name']} ({s['plain']}): {s['why']}")
            for c in s["checks"]:
                lines.append(self._check_line(c))
        lines.append("Any time:")
        lines += [self._check_line(c) for c in v["anytime"]]
        if v["next"] and v["next"].get("check"):
            lines.append(f"Next: {v['next']['name']} ({v['next']['check']}).")
        return "\n".join(lines)

    @staticmethod
    def _check_line(c: dict) -> str:
        last = f"last run {c['last_run'][:10]}, {c['open']} open" if c["last_run"] else "not run yet"
        return f"- {c['name']} [{c['id']}]: {c['says']} ({last})"

    def _estimate(self, c: dict, b: Book) -> str:
        room = self.room()
        units = sum(len(engine.units_for(n, b, room - 600, "a change")) for n in c["blocks"] if n in ("thread", "character", "section", "theme", "claim", "change", "book", "speakers", "opening", "ending"))
        cands = sum(1 for name in c["find"] for x in engine.finders.run(name, b) if x.get("judge"))
        calls = int((units + cands) * 1.4) + (1 if c["letter"] else 0)
        if not calls:
            return "It uses no brain calls, so it takes moments."
        left = self.timing.left(calls)
        return f"That is about {calls} calls to your bot's brain, " + (f"{_when_left(left).replace(' left', '')} at the speed I have measured." if left is not None else "and I have not timed your bot yet.")

    def desk_run(self, title: str = "", check: str = "", confirm: Any = False, plan: str = "") -> str:
        cs = engine.load_checks()
        c = cs.get(str(check or "").strip().lower())
        if c is None:
            raise Refused("There is no check by that name. The checks are: " + ", ".join(cs) + ".")
        folder = self._folder(title)
        b = Book(folder)
        if confirm is not True:
            return f"This has not started. Ask the writer: \"Shall I run {c['name']} on {b.title}?\" {self._estimate(c, b)} Run it only after she says yes, with confirm true."
        job, _c = self.start(b.title, c["id"], plan)
        return f"{c['name']} is in line for {job['title']}. It runs in the background; {self._estimate(c, b)}"

    def desk_status(self, title: str = "") -> str:
        folder = self._folder(title)
        st = self.status_of(folder)
        if not st:
            return "Your editor has not run anything on this book yet."
        bits = [st.get("step") or ""]
        if st.get("total"):
            bits.append(f"step {st.get('done')} of {st.get('total')}")
        if st.get("left"):
            bits.append(st["left"])
        if st.get("in_line"):
            bits.append("then " + ", ".join(st["in_line"]))
        return f"{st.get('check_name') or ''}: " + ", ".join(b for b in bits if b)

    def desk_findings(self, title: str = "", state: str = "open", check: str = "") -> str:
        folder = self._folder(title)
        engine.carry_old_desk(folder)
        want = str(state or "open").strip().lower().replace(" ", "_")
        if want not in engine.STATES:
            raise Refused("Say open, fixed, not a problem or later.")
        cs = engine.load_checks()
        every = [f for f in self.desk_view(folder.name)["findings"] if not check or f.get("check") == check]
        items = [f for f in every if f.get("state") == want and f.get("kind") != "question"]
        asks = [f for f in every if f.get("state") == "open" and f.get("kind") == "question"]
        tail = f"\nAlso {len(asks)} question{'s' if len(asks) != 1 else ''} for the writer to look at, not counted as problems." if asks and want == "open" else ""
        if not items:
            return f"No {want.replace('_', ' ')} findings." + tail
        lines = [f"{len(items)} {want.replace('_', ' ')}, biggest first."]
        for i, f in enumerate(items, 1):
            spot = "; ".join(f"section {w['section']}: \"{w['quote']}\"" for w in f.get("where") or [] if w.get("quote"))
            lines.append(f"{i}. ({f.get('size')}, {(cs.get(f.get('check')) or {}).get('name', '')}) {f.get('what')} Where: {spot or 'see its sections'}. Why it matters: {f.get('why')} One way to fix it: {f.get('fix')} [{f.get('id')}]")
        return "\n".join(lines) + tail

    def desk_mark(self, title: str = "", id: Any = "", state: str = "", note: Any = None) -> str:  # noqa: A002
        return self.mark(title, id, state, note)[0]

    def desk_letter(self, title: str = "") -> str:
        v = self.desk_view(title)
        lt = v["letter"]
        plan = "\n".join(f"{i}. {p['what']} ({p['stage']})" for i, p in enumerate(v["plan"], 1))
        if not lt:
            return f"Your editor has not written a letter yet: it comes with the first check. Next: {(v['next'] or {}).get('name', '')}."
        working = "\n".join(f"- {x}" for x in lt.get("working") or [])
        byid = {f["id"]: f for f in v["findings"]}
        shown = [p for p in lt.get("problems") or [] if p.get("id") in byid and byid[p["id"]].get("kind") != "question"]   # never one since dropped
        probs = "\n".join(f"{i}. {p['line']}" + (" (You marked this Fixed.)" if byid[p["id"]].get("state") == "fixed" else "") for i, p in enumerate(shown, 1))
        return f"{lt.get('book')}\n\nWhat is working:\n{working}\n\nThe biggest problems:\n{probs}\n\nWhat to do first: {lt.get('first')}\n\nThe revision plan as it stands:\n{plan}"

    def desk_documents(self, title: str = "", name: str = "") -> str:
        folder = self._folder(title)
        docs = engine.load_documents(folder)
        if not docs:
            return "No documents yet. They come from Style sheet, Length and genre fit, Synopsis and query letter, and Submission format."
        if name:
            low = str(name).lower()
            d = next((x for x in docs if low in str(x.get("name")).lower() or low in str(x.get("kind"))), None)
            if d is None:
                raise Refused("I could not find that document.")
            got = self.document_view(folder.name, d["id"])
            return got.get("text") or f"{d['name']} is a Word file: {got['where']}."
        return "\n".join(f"{d.get('name')} ({str(d.get('made_at'))[:10]})" for d in docs)

    ASK_SYSTEM = ("You help a novelist think about a change to her own book, from her editor's list of what the change touches and "
                  "the story bible. Answer in plain words, naming sections like (section 3). When the facts don't say, say so. "
                  "Never write new story text.")

    def desk_ask(self, title: str = "", question: str = "") -> str:
        q = " ".join(str(question or "").split())[:2000]
        if not q:
            raise Refused("Say what you want to know about the plan.")
        folder = self._folder(title)
        b = Book(folder)
        items = [f for f in engine.load_findings(folder) if f.get("check") == "future"]
        room = self.room() - tokens(self.ASK_SYSTEM) - tokens(q) - 100
        facts = [f"Change she asked about: {f.get('plan')}. Revisit (section {(f.get('where') or [{}])[0].get('section')}): {f.get('what')}" for f in items]
        user = f"Question: {q}\n\nWhat the plan touches:\n" + (engine.fit(facts, room // 2) or "(no plan traced yet)")
        user += "\n\nStory bible:\n" + engine.fit(engine.bible_lines(b, ("summary", "threads", "characters")), room // 2)
        messages = [{"role": "system", "content": self.ASK_SYSTEM}, {"role": "user", "content": user}]
        job = self.queue.submit(lambda: self.brain.chat(messages, patient=False))    # ahead of a check's next call
        try:
            text, _m = job.wait(ASK_WAIT)
        except (NotYet, BrainBusy):
            self.queue.cancel(job)
            return "Your editor is in the middle of a long ask. Try again in a few minutes."
        except BrainDown as exc:
            raise Refused(str(exc)) from None
        except BrainSlow:
            raise Refused("Your bot's brain took too long on that. Try a smaller question.") from None
        return strip_think(text) or "Your bot had no answer to that."

    def desk_stop(self, title: str = "") -> str:
        return self.stop(title)


_Run = engine.Run


# -- MCP ------------------------------------------------------------------------------------------

CHANGES = ("desk_run", "desk_mark", "desk_stop")
_TITLE = {"type": "string", "description": "The manuscript's title. May be left empty when there is only one."}
TOOLS = {
    "desk_checks": ("The stages of editing for one manuscript and every check in them, when each last ran, what it found, and which comes next.", {"title": _TITLE}, []),
    "desk_run": ("Runs one editing check in the background. It starts only after the writer said yes; asked without her yes it only tells your bot what to ask her. A check can take a long time.",
                 {"title": _TITLE, "check": {"type": "string", "description": "The check's id from desk_checks, such as plot, continuity or future."},
                  "confirm": {"type": "boolean", "description": "True only when the writer said yes to this check, in her latest message."},
                  "plan": {"type": "string", "description": "What she typed for a check with a box: the change she is thinking of, her genre, or her name for the title page."}}, ["check"]),
    "desk_status": ("What the writer's editor is doing now for one manuscript, how far along, and about how long is left.", {"title": _TITLE}, []),
    "desk_findings": ("What the checks found in one manuscript: each with what is wrong, the section and its exact quote, why it matters and one way to fix it, biggest first.",
                      {"title": _TITLE, "state": {"type": "string", "enum": ["open", "fixed", "not a problem", "later"]}, "check": {"type": "string", "description": "Only this check's findings."}}, []),
    "desk_mark": ("Marks one finding Open, Fixed, Not a problem or Later, and keeps a note of the writer's on it, by its number among the open ones or its id.",
                  {"title": _TITLE, "id": {"type": "string", "description": "The finding's number or id."}, "state": {"type": "string", "enum": ["open", "fixed", "not a problem", "later"]}, "note": {"type": "string"}}, ["id"]),
    "desk_letter": ("The Editor's letter for one manuscript: what the book is, what is working, its biggest problems, what to do first, and the revision plan as it stands now.", {"title": _TITLE}, []),
    "desk_ask": ("Answers a question about a change the writer is planning, from what Future plans and changes found and the story bible.", {"title": _TITLE, "question": {"type": "string"}}, ["question"]),
    "desk_documents": ("The documents made for one manuscript: the style sheet, length note, synopsis, query letter, submission copy. Gives one by name, or the list.", {"title": _TITLE, "name": {"type": "string"}}, []),
    "desk_stop": ("Stops the check running for one manuscript, after the call in progress. Pressed again, it goes on from where it was.", {"title": _TITLE}, []),
}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc, "annotations": {"readOnlyHint": name not in CHANGES, "destructiveHint": False},
             "inputSchema": {"type": "object", "properties": props, "required": need, "additionalProperties": False}}
            for name, (desc, props, need) in TOOLS.items()]


def mcp_answer(app: Desk, msg: dict) -> dict | None:
    method = msg.get("method") if isinstance(msg, dict) else None
    mid = msg.get("id") if isinstance(msg, dict) else None
    if mid is None and method:
        return None                          # a notification

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return ok({"protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": NAME, "version": VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS or not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "No such tool."}}
        try:
            text = getattr(app, name)(**{k: v for k, v in args.items() if k in TOOLS[name][1]})
            return ok({"content": [{"type": "text", "text": text}]})
        except Refused as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except OSError as exc:
            return ok({"content": [{"type": "text", "text": f"I could not reach that file ({type(exc).__name__}). Nothing was changed."}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "No such method."}}


# -- HTTP -----------------------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "editors-desk/" + VERSION
    protocol_version = "HTTP/1.1"
    app: Desk
    token = ""

    def log_message(self, fmt, *args):     # quiet; the Core keeps the logs it needs
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self._sent = True
        self.send_response(code)
        if body or code != 202:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _drain(self) -> bytes:
        """This request's body, read once before any reply (a reply over an unread body resets the connection on Windows)."""
        if self._raw is None:
            raw_len = self.headers.get("Content-Length") or "0"
            n = int(raw_len) if raw_len.isascii() and raw_len.isdigit() else 0
            self._too_big = n > MAX_BODY
            if self._too_big:
                self.close_connection = True
                self._raw = b""
            else:
                self._raw = self.rfile.read(n) if n > 0 else b""
        return self._raw

    def _token_ok(self) -> bool:
        if not self.token:
            return False                     # no token given at start: refuse every call
        got = self.headers.get("Authorization", "")
        got = got[7:] if got.lower().startswith("bearer ") else self.headers.get("X-PuffPAI-Key", "")
        return hmac.compare_digest(got.encode(), self.token.encode())

    def _refused(self) -> bool:
        if self._token_ok():
            return False
        self._drain()
        self._json(401, {"error": "Editor's Desk needs its PuffPAI token. Open it from the PuffPAI Desktop."})
        return True

    def _guard(self, fn) -> None:
        self._sent = False
        self._raw, self._too_big = None, False     # a new request on this connection: its own body
        try:
            fn()
        except Refused as exc:
            if not self._sent:
                self._json(400, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - the answer says what kind of error it was
            self.close_connection = True
            if not self._sent:
                try:
                    self._json(500, {"error": f"Editor's Desk stopped on this request ({type(exc).__name__}). Nothing was changed."})
                except Exception:  # noqa: BLE001 - the connection itself is gone
                    pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._guard(self._get)

    def _get(self):
        if self._refused():
            return
        path, _, query = self.path.partition("?")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
        a = self.app
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/health":
            return self._send(200, b"ok", "text/plain")
        if path == "/mcp":
            return self._send(405, b"", "text/plain")      # no server-sent stream; POST only
        if path == "/api/list":
            return self._json(200, a.list_view())
        if path == "/api/desk":
            return self._json(200, a.desk_view(q.get("title", "")))
        if path == "/api/section":
            return self._json(200, a.section_view(q.get("title", ""), q.get("n", "")))
        if path == "/api/version":
            return self._json(200, a.version_view(q.get("title", ""), q.get("n", ""), q.get("id", "")))
        if path == "/api/document":
            return self._json(200, a.document_view(q.get("title", ""), q.get("id", "")))
        if path == "/api/settings":
            return self._json(200, {"settings": a.settings()})
        name = urllib.parse.unquote(path.lstrip("/"))
        file = (WEB / name).resolve()
        if name and file.suffix.lower() in WEB_TYPES and WEB.resolve() in file.parents and file.is_file():
            return self._send(200, file.read_bytes(), WEB_TYPES[file.suffix.lower()])   # the page's own files
        self._json(404, {"error": "Editor's Desk has no page here."})

    def do_POST(self):
        self._guard(self._post)

    def _post(self):
        path = self.path.split("?", 1)[0]
        if self._refused():
            return
        raw = self._drain()
        if self._too_big:
            return self._json(413, {"error": "That is too big for Editor's Desk."})
        try:
            msg = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(msg, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            if path == "/mcp":
                return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Not JSON."}})
            return self._json(400, {"error": "Not JSON."})
        a = self.app
        if path == "/mcp":
            answer = mcp_answer(a, msg)
            return self._send(202) if answer is None else self._json(200, answer)
        if path == "/api/open":
            return self._json(200, a.open_book(msg.get("title")))
        if path == "/api/run":
            job, c = a.start(msg.get("title"), msg.get("check"), msg.get("plan"))
            return self._json(200, {"message": f"{c['name']} is in line.", "job": job})
        if path == "/api/stop":
            return self._json(200, {"message": a.stop(msg.get("title"))})
        if path == "/api/mark":
            line, f = a.mark(msg.get("title"), msg.get("id"), msg.get("state"), msg.get("note"))
            return self._json(200, {"message": line, "finding": f})
        if path == "/api/save":
            return self._json(200, a.save(msg))
        if path == "/api/restore":
            return self._json(200, a.restore(msg))
        if path == "/api/use" and USE_THIS:
            return self._json(200, a.use_this(msg))
        if path == "/api/document":
            return self._json(200, a.document_save(msg))
        if path == "/api/settings":
            return self._json(200, a.settings_save(msg))
        self._json(404, {"error": "Editor's Desk has no page here."})

    def do_DELETE(self):
        self._guard(self._no)

    def do_PUT(self):
        self._guard(self._no)

    def _no(self):
        if not self._refused():
            self._drain()
            self._send(405, b"", "text/plain")


def make_server(port: int, files: Any, token: str | None, brain: Brain | None = None) -> ThreadingHTTPServer:
    """The server, bound to 127.0.0.1 only. Call serve_forever(); stop with shutdown()."""
    handler = type("EditorsDeskHandler", (Handler,), {"app": Desk(files, brain), "token": token or ""})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Editor's Desk for PuffPAI")
    p.add_argument("--port", type=int, default=int(os.environ.get("PUFFPAI_PORT") or 0))
    args = p.parse_args(argv)
    token = os.environ.get("PUFFPAI_TOKEN")
    api = os.environ.get("PUFFPAI_API")
    if not args.port or not api:
        p.error("PuffPAI starts Editor's Desk with PUFFPAI_PORT (or --port) and PUFFPAI_API set.")
    server = make_server(args.port, CoreFiles(api, token), token, Brain(api, token))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
