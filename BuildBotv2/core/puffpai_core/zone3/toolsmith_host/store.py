# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/yes.py (rewritten-from)
r"""The build store, kept by the Core: ``builds\<id>\build.json`` and the job status set (03 3.6).

Toolsmith's process never writes these files. It sends its plan and its job
status over its Toolsmith-token routes, and the Core checks every field here
before it is kept:
- :meth:`Builds.set_plan` takes the jobs and candidates Toolsmith found, keeps
  only the fields a plan has (a candidate's address is rebuilt from its
  host and ``owner/repo``, so it can only be a github.com or codeberg.org
  project), works out the ONE
  setup list from the programs the first candidates need, and the envelope
  and its sha; :func:`card` writes the Yes card from that data. No sentence
  on the card comes from Toolsmith.
- An **offer** (lane I4b): when only one of the brain's two asks picked an
  installed package for a job, the plan carries ``offer`` (that package and
  the tool it would use). The card shows it as a line she can tick, unticked
  by default. Her tick is recorded here by the Core (:meth:`Builds.set_use`,
  from her answer to the card), never by Toolsmith, and only while the card
  is still waiting. The moment the card is granted the Core copies the ticks
  into ``use_fixed`` (:meth:`Builds.fix_use`, the consent store's own Yes
  listener); after that no tick changes, and ``ToolsmithHost.start`` reads only
  that copy. No tick: the job searches as planned.
- A **store suggestion** (lane I10c): when both of the brain's asks picked a
  tool of an app in the PuffPAI store (built in, not installed), the plan
  carries ``store``. The card shows it unticked ("<App> is in the PuffPAI
  store: it can ... Tick to add it."); her tick goes through the same
  ``set_use``. At her Yes the job uses that app and never searches; the app is
  added through the Package Manager, with its own Yes card, before the build.
  A suggestion can also be a verified row of the official store's saved index (D96): the plan's ``package`` is
  then ``store:<id>`` (what adds it), and the Registry key is the bare id (:func:`bare_id`).
- A **model offer** (lane I17): when one small local ask says the goal needs a coding or picture model and
  none is set, the card offers the best fitting catalog model as a line she can tick ("Add a coding model:
  ..."), or says plainly that none fits. Her tick goes through the same ``set_use`` (``model:<role>``); at
  her Yes the Core downloads it with its own Yes card and sets the role before the build (``store_phase``).
- :meth:`Builds.job_update` takes one status write: a state from the one set,
  a short line, what was tried, a result. Proof that a package works for a
  job (``proven.json``) is kept only for a package this build installed or
  reused.

Job status, always derived from the live run: ``planned``, ``waiting_yes``,
``waiting_you``, ``waiting_vault``, ``held``, ``building``, ``working``,
``not_yet``, ``nothing_found``. An active try shows ``building: <app>``. A job whose answer is a free site, with
nothing installed, shows ``free_site`` (D121): only a status to read, its run state stays ``working``.
v1 source: ``build/yes.py`` (``sha`` :65 over the plan body; the Yes record).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import policy
from ..toolsmith import adopt, find, programs, shelves
from ..toolsmith.names import cut_text
from . import interpret as interpret_mod, knowledge as knowledge_mod, settings as settings_mod

STATES = ("planned", "waiting_yes", "waiting_you", "waiting_vault", "held", "building", "working",
          "not_yet", "nothing_found")
ACTIVE = ("planned", "building", "waiting_you", "waiting_vault")
#: The jobs "Check again" tries again (lane I18e): each ended without an app working for it.
UNFINISHED = ("held", "not_yet", "nothing_found")
#: How a build ends when its plan card gets no Yes (lane I6b). Only the Core writes these, from her answer
#: in the consent store; Toolsmith's job_update can't send them.
NO_YES_ENDS = ("said_no", "no_answer", "replaced")
SAID_NO_LINE = "You said No, so nothing was built."
NO_ANSWER_LINE = "The plan's Yes card got no answer in time, so nothing was built."
#: A newer plan for the same goal took this one's place (lane I8a): its card is closed, nothing of it is built.
REPLACED_LINE = "Replaced by a newer plan."
NOTHING_LINE = "Nothing found yet. Paste a GitHub address for this job."
EMPTY_PLAN_LINE = "Toolsmith found nothing to install, so there is nothing to say Yes to."


nothing_line = find.nothing_line
_FULL = re.compile(r"^(?!\.+/)[A-Za-z0-9_.-]{1,100}/(?!\.+$)[A-Za-z0-9_.-]{1,100}$")
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
MAX_RESULT_BYTES = 20_000


def agreed_choice(choice: dict) -> dict:
    """What the plan card said about Toolsmith's model (lane I18e): this computer, or the company, its model and
    the cost cap. "Check again" reuses her Yes only while this is still the same."""
    if (choice or {}).get("where") != "cloud":
        return {"where": "local"}
    try:
        cap = float(choice.get("cost_cap_usd"))
    except (TypeError, ValueError):
        cap = None
    return {"where": "cloud", "provider": choice.get("provider"), "model": choice.get("model"), "cost_cap_usd": cap}


def again_hosts(b: dict, job_ids: list[str], cloud: bool = False) -> list[str]:
    """The install hosts a "Check again" card covers (lane I18e): only those of the jobs tried again, and never
    one the plan's own envelope did not list. *cloud*: all the plan's, since a job there may find any app (D124)."""
    listed = set(((b.get("envelope") or {}).get("install_hosts")) or [])
    if cloud:
        return sorted(listed)
    cands = [c for j in b.get("jobs") or [] if j.get("id") in job_ids
             for c in (j.get("plan") or {}).get("candidates") or []]
    want = {adopt.project_host(c) for c in cands} | {h for c in cands
                                                     for h in adopt.INSTALL_HOSTS.get(c.get("runtime"), [])}
    return sorted(want & listed)


def bridge_target(build_id: str) -> str:
    """The build's name on its plan Yes card and on the Bridge (``pkg:<this>`` for its tries).

    Short on purpose: the Bus's floor key check (D50) reads a build id's date and time digits as a
    secret and drops every message that carries them, so a try's proxied downloads were refused. The
    build id's own random part keeps it apart from other builds (integration lane I2a)."""
    return "toolsmith-build-" + str(build_id).rsplit("-", 1)[-1]


def sha_of(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _s(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def bare_id(package: Any) -> str:
    """The Registry's id for a plan's store ``package``: the id itself, or the ``<id>`` of ``store:<id>`` (D96)."""
    text = str(package or "")
    return text[len("store:"):] if text.startswith("store:") else text


def _app_name(name: Any, package: str) -> str:
    """An app's name as a card shows it (lane I14): its own name when that reads as plain words
    (``text_rules.owner_text_flag``), else its package id."""
    from ...zone3.text_rules import owner_text_flag
    said = _s(name, 80)
    return said if said and not owner_text_flag(said) else package


def _pasted(urls: Any) -> list[str]:
    """Her pasted addresses, each as https://github.com/owner/repo (any common GitHub form), once each."""
    out: list[str] = []
    for u in (urls if isinstance(urls, list) else [])[:5]:
        url = find.github_url(u) if isinstance(u, str) else ""
        if url and url not in out:
            out.append(url)
    return out


def _candidate(c: Any) -> dict | None:
    """One candidate as the Core keeps it (None: not a github.com or codeberg.org project)."""
    if not isinstance(c, dict) or not _FULL.match(str(c.get("full_name") or "")):
        return None
    host = c.get("host") or "github.com"
    if host not in adopt.PROJECT_HOSTS:
        return None
    full = str(c["full_name"])
    runtime = str(c.get("runtime") or "")
    repo = full.split("/")[-1]
    return {"ref": f"{'codeberg' if host == 'codeberg.org' else 'github'}:{full}",
            # Outside words on her card (npm, PyPI, Codeberg, a web result): the owner screen, or the repo's own name.
            "name": _s(knowledge_mod._safe_title(c.get("name") or repo, repo), 80), "full_name": full, "host": host,
            "url": f"https://{host}/{full}", "stars": c["stars"] if isinstance(c.get("stars"), int) and c["stars"] >= 0 else 0,
            "description": _s(c.get("description"), 300), "language": _s(c.get("language"), 40),
            "license": _s(c.get("license"), 40) or None, "branch": _s(c.get("branch"), 100),
            "readme_steps": [_s(x, 300) for x in (c.get("readme_steps") or [])[:20] if isinstance(x, str)],
            "runtime": runtime if runtime in ("node", "python", "docker") else "",
            "readme_port": c.get("readme_port") if isinstance(c.get("readme_port"), int) else None,
            "docker_file": _s(c.get("docker_file"), 60),
            "tries_in": "docker" if c.get("tries_in") == "docker" else "native",
            "programs": [p for p in (c.get("programs") or []) if p in programs.PACKAGE_IDS][:10],
            "source": c.get("source") if c.get("source") in ("github", "awesome-selfhosted", "pasted", "codeberg", "npm",
                                                             "pypi", "brave", "marginalia", find.ORIGIN) else "github"}


def _offer(o: Any, installed: set[str] | None) -> dict | None:
    """An offer as the Core keeps it: an installed package the brain picked on one of the two asks."""
    if not isinstance(o, dict) or installed is None or not isinstance(o.get("package"), str):
        return None
    pkg = o["package"][:40]
    if pkg not in installed:
        return None
    return {"package": pkg, "name": _app_name(o.get("name"), pkg), "tool": _s(o.get("tool"), 120),
            "ask": _s(o.get("ask"), 300),
            "picked": [{"ask": _s(x.get("ask"), 300), "tool": _s(x.get("tool"), 120)}
                       for x in (o.get("picked") or [])[:2] if isinstance(x, dict)]}


def _store_offer(o: Any, shop: list[dict] | None) -> dict | None:
    """A PuffPAI store suggestion as the Core keeps it (lane I10c): a store app (built in, kind app, not installed;
    or a verified row of the official store's saved index, D96) whose tool the brain picked on both of the job's
    asks. Its name and ``package`` are the store's own, never Toolsmith's: an id not on *shop* is no suggestion."""
    if not isinstance(o, dict) or not shop or not isinstance(o.get("package"), str):
        return None
    app = next((a for a in shop if a.get("id") == bare_id(o["package"])), None)
    if app is None:
        return None
    tool = _s(o.get("tool"), 120)
    if tool not in {str(a.get("tool") or "") for a in app.get("abilities") or []}:
        tool = ""
    return {"package": str(app.get("package") or app["id"]), "name": _app_name(app.get("name"), app["id"]), "tool": tool,
            "picked": [{"ask": _s(x.get("ask"), 300), "tool": _s(x.get("tool"), 120)}
                       for x in (o.get("picked") or [])[:2] if isinstance(x, dict)]}


def store_line(j: dict, shop: list[dict] | None = None) -> str:
    """The plan card's line for a PuffPAI store suggestion (lane I10c), unticked: what its tool does, in the
    first sentence of the store's own description, screened like any tool's words (``find.tool_does``)."""
    o = j["plan"].get("store") or {}
    does = find.tool_does(o.get("tool"), shop)
    name = _app_name(o.get("name"), bare_id(o.get("package")))
    said = f"{name} is in the PuffPAI store: it can {does}." if does else \
        f"{name} is in the PuffPAI store, with a tool for this job."
    return f"{j['name']}: {said} Tick to add it."


def _note(n: Any) -> dict | None:
    if not isinstance(n, dict):
        return None
    out = {"name": _s(n.get("name"), 100), "ref": _s(n.get("ref"), 220), "why": _s(n.get("why"), 300)}
    if _s(n.get("detail"), 300):
        out["detail"] = _s(n.get("detail"), 300)    # the build log's detail (tool names, the ask); never on a card
    return out


def job_status(b: dict, j: dict) -> str:
    """The one status set (03 3.6), from the live run."""
    run = j.get("run") or {}
    state = run.get("state") or "planned"
    if state == "working" and (run.get("result") or {}).get("site"):
        return "free_site"                         # D121: its answer is a free site, so no app runs: never "working"
    if run.get("trying") and state == "building":
        return f"building: {run['trying']}"
    if not b.get("yes") and state in ("planned", "waiting_yes"):
        return "waiting_yes" if (b.get("consent") or {}).get("id") else "planned"
    return state if state in STATES + NO_YES_ENDS else "planned"


def library_job(j: dict) -> bool:
    """D123, the one place: this job has no app, only some reading in the library (its ``working`` came from the
    library check, D71). Any other ``working`` job (an app, a free site) is not one. Each screen reads this as
    ``library: true`` on the job, in the build view, the build list and the Prove and Done results."""
    run = j.get("run") or {}
    return run.get("state") == "working" and (run.get("result") or {}).get("by") == "knowledge"


def offer_line(j: dict, rows: list[dict] | None = None) -> str:
    """The plan card's line for an offer (one brain ask picked an installed package's tool): she may tick it.
    *rows*: the Registry's rows, so the tool is said by what it does, never by its name (lane I8a)."""
    o = j["plan"].get("offer") or {}
    does = find.tool_does(o.get("tool"), rows)
    return (f"{j['name']}: {_app_name(o.get('name'), str(o.get('package') or ''))} may cover this"
            + (f": it would {does}." if does else f": {find.NO_TOOL_WORDS}.") + " Tick to use it instead of searching.")


#: Said on the plan card right under the GitHub search words, as the knowledge topics have theirs (lane I15).
GITHUB_SENT_LINE = "These words are sent to GitHub as searches."
#: Said on every plan card that uses a cloud model (D68, lane I19a): what her Yes lends, and for how long.
KEY_KEPT_LINE = ("When you say Yes, Toolsmith keeps the key it needs until this build ends, you lock the vault, or "
                 "you close BuildBot (3 hours at most).")


def found_line(company: str = "") -> str:
    """The plan card's line on a cloud plan for what her Yes also lets Toolsmith do while it builds (D121), and what goes
    to *company* because of it. A part goes when its switch is off. Her bot's names go out either way, since the job's
    look reads them. On the fingerprinted card, so ``card_sha`` covers it."""
    reads, tries = policy.READ_PUBLIC_PAGES, policy.TRY_FOUND_APPS
    parts = []
    if reads:
        parts.append("After your Yes, Toolsmith may read any public web page while it builds, only to read, never "
                     "signing in or sending your files.")
    if tries:
        parts.append(("It may also find other apps than the ones listed here and try them the same way, each as a "
                      "program on this computer, as you." if reads else
                      "After your Yes, Toolsmith may find other apps than the ones listed here and try them, each as a "
                      "program on this computer, as you.") + " Before it keeps one of those, its own card asks you.")
    who = company or "the company"
    parts.append(f"The pages it reads and the names of what is on your bot go to {who} too." if reads else
                 f"The names of what is on your bot go to {who} too.")
    return " ".join(parts)


def search_words(searches: Any) -> list[str]:
    """The GitHub search words the Core sent for this plan, in order, each once."""
    items = searches if isinstance(searches, list) else []
    return list(dict.fromkeys(_s(w, 80) for w in items[:40] if isinstance(w, str) and _s(w, 80)))


def _rows_by_id(owner: dict | None = None) -> dict[str, dict]:
    """Every place Toolsmith may look, by its row id (D99), and the APIs she added herself (*owner*, D107)."""
    src = find.sources()
    return {**(owner or {}), **(src.get("catalogs") or {}), **(src.get("web") or {}), **(src.get("shelves") or {})}


def _named(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def search_lines(searches: Any, by_place: Any = None, owner: dict | None = None) -> list[str]:
    """The plan card's search section: every search's words the Core sent for this plan, in order, per place. The
    last line names every place her words went, with its host for each new place (D99)."""
    words = search_words(searches)
    lines = ["GitHub searches: " + ", ".join(words) + "."] if words else []
    rows = _rows_by_id(owner)
    places = ["GitHub"] if words else []
    for rid, said in (by_place if isinstance(by_place, dict) else {}).items():
        row = rows.get(rid)
        kept = search_words(said)
        if row and kept:
            short = str(row.get("short") or row["name"])
            lines.append(f"{short} searches: {', '.join(kept)}.")
            places.append(f"{short} ({row['host']})")
    if not lines:
        return []
    return lines + [GITHUB_SENT_LINE if places == ["GitHub"] else f"These words are sent to {_named(places)} as searches."]


def places_lines(places: Any, whole: Any = None, late: Any = None, places_asked: Any = None,
                 owner: dict | None = None) -> list[str]:
    """The card's lines on where it looked and did not (D99): every place searched, lists read whole with no word
    sent, places her search Yes does not name, places that gave no answer, places that need her key. In plain words,
    each place by name."""
    rows = _rows_by_id(owner)
    said = places if isinstance(places, dict) else {}
    if not said:
        return []                                  # no job searched: words sent, if any, are on the search lines
    name = lambda rid: str(rows[rid].get("short") or rows[rid]["name"])    # noqa: E731
    lines = []
    asked = [r for r in dict.fromkeys(list(said.get("asked") or []) + list(places_asked or [])) if r in rows]
    first = ["GitHub", "awesome-selfhosted"] if said.get("github", True) else []   # every job reused: it was not asked
    if (said or asked) and first + asked:
        lines.append("Searched for this plan: " + _named(first + [name(r) for r in asked]) + ".")
    read = [r for r in dict.fromkeys(whole if isinstance(whole, list) else []) if r in rows]
    if read:
        lines.append("Read whole, with none of your words sent: "
                     + _named([f"{name(r)} ({rows[r]['host']})" for r in read]) + ".")
    missing = [r for r in dict.fromkeys(said.get("not_asked") or []) if r in rows]
    if missing:
        lines.append(f"Not asked, since your search Yes does not name them yet: {_named([name(r) for r in missing])}. "
                     "To add them, turn Toolsmith's search off and on again under Developer, Zone 3 Bridge, and say Yes.")
    down = [r for r in dict.fromkeys(list(said.get("down") or []) + list(late or [])) if r in rows]
    line = shelves.down_line([name(r) for r in down])
    if line:
        lines.append(line)
    keyed = [r for r in dict.fromkeys(said.get("needs_key") or []) if r in rows]
    if keyed:
        lines.append(f"Skipped, since each needs your own key: {_named([name(r) for r in keyed])}. Web search stays off "
                     "until you link one.")
    if said.get("ran_late") is True:
        lines.append("The time for this plan ran out, so Toolsmith wrote it with what it had found.")
    return lines


def _look_places(owner: dict | None = None) -> dict[str, dict]:
    """The places a row to look at may come from: every place, and GitHub for a part of another app (D116)."""
    return {**_rows_by_id(owner), "github": {"name": "GitHub"}}


def no_steps_line(cands: list[dict]) -> str:
    """Which found candidates Toolsmith works out from their files (D116), or ""."""
    names = [c["name"] for c in cands if not c.get("readme_steps") and c.get("tries_in") != "docker"]
    if not names:
        return ""
    if len(names) == 1:
        return f" The README of {names[0]} does not say how to start it, so Toolsmith works that out from its files."
    return f" The READMEs of {_named(names)} do not say how to start them, so Toolsmith works that out from their files."


def look_rows(plan: Any, owner: dict | None = None) -> list[dict]:
    """A job's rows to look at, as the Core keeps them: never tried, never ticked. Outside words: a name the owner
    screen flags is left out, and so is any address that is not a plain https one."""
    from ..text_rules import url_is_plain
    rows = _look_places(owner)
    out = []
    for r in (plan.get("look") if isinstance(plan, dict) and isinstance(plan.get("look"), list) else [])[:find.PER_JOB]:
        if not isinstance(r, dict) or r.get("source") not in rows:
            continue
        name = knowledge_mod.shown_look(r.get("name"))
        url = str(r.get("url") or "")
        if name and url.startswith("https://") and url_is_plain(url) and len(url) <= 300 and "@" not in url:
            out.append({"name": name, "source": str(r["source"]), "url": url})
    return out


def look_line(j: dict, owner: dict | None = None) -> str:
    """The card's honest line for rows Toolsmith can't try: named, with where they are, and no tick."""
    rows = _look_places(owner)
    found = [f"{r['name']} ({rows[r['source']].get('short') or rows[r['source']]['name']}, {r['url']})"
             for r in (j["plan"].get("look") or [])]
    return f"{j['name']}: to look at, Toolsmith can't try these as apps, so nothing here is ticked: {'; '.join(found)}." \
        if found else ""


def held_line(words: Any, what: str) -> str:
    """The owner's own line for searches or topics held because they used a name from the household (lane
    I15): "1 search was not sent because it used a name from your household: will planning." Her screen only."""
    got = [_s(w, 80) for w in (words if isinstance(words, list) else [])[:20] if isinstance(w, str) and _s(w, 80)]
    if not got:
        return ""
    one = len(got) == 1
    noun = {"searches": ("search", "searches"), "topics": ("knowledge topic", "knowledge topics")}[what][0 if one else 1]
    return (f"{len(got)} {noun} {'was' if one else 'were'} not sent because {'it' if one else 'they'} used a name "
            f"from your household: {', '.join(got)}.")


def _has_app(plan: dict) -> bool:
    """True when the job already has its app: reused, offered from the Registry or the store, or found."""
    return bool(plan.get("reuse") or plan.get("offer") or plan.get("store") or plan.get("candidates"))


def _given(plan: dict) -> bool:
    """True when the job's app is settled: reused, offered, or only the project she pasted."""
    cands = plan.get("candidates") or []
    return bool(plan.get("reuse") or plan.get("offer") or plan.get("store"))         or (bool(cands) and all(c.get("source") == "pasted" for c in cands))


def no_topics_said(jobs: list[dict], asked: dict | None = None) -> bool:
    """Whether a plan with no knowledge topics says so (D84): only when a job has no app found or reused (it
    could need the library), or a topic ask got no answer or timed out. Never when every job is a pasted
    address or an app already on the bot."""
    plans = [j.get("plan") or {} for j in jobs if not j.get("pasted")]    # a pasted app's job asks no topics (D104)
    if plans and all(_given(p) for p in plans):
        return False
    asked = asked if isinstance(asked, dict) else {}
    failed = any(isinstance(asked.get(k), int) and asked[k] > 0 for k in ("no_answer", "timed_out"))
    return failed or any(not _has_app(p) for p in plans)


def _tries(cand: dict) -> str:
    """Where it runs, inside the card line's own brackets: its last full stop is the line's."""
    return find.tries_line(cand).rstrip(".")


def card(goal: str, jobs: list[dict], setup: list[dict], choice: dict, company: str = "",
         rows: list[dict] | None = None, shop: list[dict] | None = None, knowledge: dict | None = None,
         searches: list[str] | None = None, held: dict | None = None, models: list[dict] | None = None,
         left_out: list[str] | None = None, limited: dict | None = None, asked: dict | None = None,
         by_place: dict | None = None, whole: list | None = None, places: dict | None = None,
         late: list | None = None, owner: dict | None = None) -> dict:
    """The ONE Yes card, written by the Core from the plan's data. *rows*: the Registry's rows, so a tool is said
    by what it does (its own description), never by its name. *shop*: the PuffPAI store's apps (lane I10c).
    *knowledge*: the plan's knowledge leg (lane I11): its Knowledge section says what is read, from which
    hosts, and roughly how big. *searches*: the GitHub search words the Core sent while planning (lane I15), so
    the owner sees them before the Yes. *held*: the searches and topics held for a household name, said in one
    plain line each (her screen only). *models*: the plan's model offers (lane I17, ``model_offer``).
    *left_out*: what she asked for that is not a job, said in one line after the jobs (D84). A *knowledge*
    leg with no topics: one plain line says no knowledge is read (D84), only when :func:`no_topics_said`.
    *asked*: the topic asks' counts (``no_answer``, ``timed_out``; D84).
    *limited*: ``{slowed, skipped}``, GitHub's limit during the plan, said in one line with where a key goes (D85).
    *by_place*, *whole*, *places*, *late*: the other places looked in and not looked in (D99, :func:`places_lines`)."""
    lines = []
    offers = []
    for j in jobs:
        p = j["plan"]
        if p["reuse"]:
            tools = [str(x.get("tool") or "") for x in p.get("picked") or [] if isinstance(x, dict) and x.get("tool")]
            why = f": {find.tools_said(tools, rows)}" if tools else ""
            lines.append(f"{j['name']}: {p.get('reuse_name') or p['reuse']}, already on your bot{why}.")
            continue
        if p.get("offer"):
            lines.append(offer_line(j, rows))
            offers.append({"job": j["n"], "job_name": j["name"], "package": p["offer"]["package"],
                           "name": _app_name(p["offer"]["name"], p["offer"]["package"]), "tool": p["offer"]["tool"],
                           "does": find.tool_does(p["offer"]["tool"], rows), "line": offer_line(j, rows)})
        elif p.get("store"):
            lines.append(store_line(j, shop))
            offers.append({"job": j["n"], "job_name": j["name"], "package": p["store"]["package"],
                           "name": _app_name(p["store"]["name"], bare_id(p["store"]["package"])), "tool": p["store"]["tool"], "store": True,
                           "does": find.tool_does(p["store"]["tool"], shop), "line": store_line(j, shop)})
        if p["candidates"] and all(c.get("source") == "pasted" for c in p["candidates"]):
            first = p["candidates"][0]
            how = "" if first.get("readme_steps") or first.get("tries_in") == "docker" else f" {find.NO_STEPS_LINE}"
            lines.append(f"{j['name']}: try {first['name']} only, the project you gave ({_tries(first)}).{how} "
                         "If it does not work, Toolsmith says why and stops.")
        elif p["candidates"] and choice.get("where") == "cloud":
            first = p["candidates"][0]                 # the company's model decides what it tries (D121): no order promised
            needs = find.needs_line(first)
            lines.append(f"{j['name']}: Toolsmith looks further, and may try {_named([c['name'] for c in p['candidates']])} "
                         f"({_tries(first)})." + no_steps_line(p["candidates"]) + (f" {needs}" if needs else ""))
        elif p["candidates"]:
            first = p["candidates"][0]
            more = len(p["candidates"]) - 1
            then = f"{more} more"
            # A program to install on the computer is said plainly (lane I18b); those are ranked last already.
            needs = find.needs_line(first)
            later = sum(1 for c in p["candidates"][1:] if find.system_needs(c))
            lines.append(f"{j['name']}: try {first['name']} first{find.found_on(first)} ({_tries(first)})"
                         + (f", then {then} if it does not work." if more else ".")
                         + no_steps_line(p["candidates"])
                         + (f" {needs}" if needs else "")
                         + (f" {later} of the others need{'s' if later == 1 else ''} a program installed on this "
                            "computer." if later else ""))
        else:
            lines.append(f"{j['name']}: {nothing_line(j, find.pasted_any(jobs), code_said=True)}" + (
                f" After your Yes, Toolsmith looks further with {company or choice.get('provider')}'s model."     # D121
                if choice.get("where") == "cloud" and not j.get("pasted") else ""))
        hers = [c["name"] for c in p["candidates"] if c.get("source") == find.ORIGIN]
        if hers:                                   # D135
            lines.append(f"{j['name']}: {find.ORIGIN}: {_named(hers)}.")
        code = [str(c.get("name") or "") for c in p.get("code") or [] if isinstance(c, dict) and c.get("name")]
        if code:                                   # found code about the job, said, never quietly below (I18b)
            lines.append(f"{j['name']}: code about this was found too, but it does not start as an app by "
                         f"itself: {', '.join(code)}. To try one, paste its GitHub address for this job.")
        lines += [ln for ln in (look_line(j, owner),) if ln]
    lines += [ln for ln in (interpret_mod.left_out_line(left_out),) if ln]
    lines += [f"Your turn: {s['title']}." for s in setup]
    kept_models = [m for m in (model_offer(x) for x in (models or [])) if m]
    lines += [m["line"] for m in kept_models]
    who = "the model on this computer"
    if choice.get("where") == "cloud":
        who = f"{company or choice.get('provider')} ({choice.get('model')}), up to ${choice['cost_cap_usd']:g} for this build"
        lines.append(KEY_KEPT_LINE)
        if choice.get("consent_changed"):          # only a bot whose saved Yes is for an earlier sentence (D118)
            lines.append(f"{choice['consent_changed']} Toolsmith sends it nothing until you say yes to that in Settings.")
        lines.append(found_line(company or str(choice.get("provider") or "")))
    n = len(jobs)
    sentence = (f"Toolsmith finds and installs apps for {n} job{'s' if n != 1 else ''}"
                f" for \"{cut_text(goal, 120, '...')}\", using {who}.")    # ends at a word or sentence (D85), says it is cut
    section = knowledge_mod.card_lines(knowledge or {})
    if section and not jobs:                       # books only (D136): no app is looked for, and no model is used
        sentence = f"Toolsmith reads public knowledge about \"{cut_text(goal, 120, '...')}\" into your bot."
        lines += section
    elif section:
        sentence = sentence[:-1] + ", and reads public knowledge into your bot."
        lines += section
    elif knowledge is not None and no_topics_said(jobs, asked):    # no topics, and it matters here (D84)
        lines.append(knowledge_mod.NO_TOPICS_LINE)
    held = held if isinstance(held, dict) else {}
    lines += [ln for ln in (held_line(held.get("topics"), "topics"),) if ln]
    searched = search_lines(searches, by_place, owner)
    lines += searched
    lines += places_lines(places, whole, late, list((by_place or {}).keys()), owner)
    limited = limited if isinstance(limited, dict) else {}
    lines += [ln for ln in (find.limit_line(limited.get("slowed"), limited.get("skipped")),) if ln]
    lines += [ln for ln in (held_line(held.get("searches"), "searches"),) if ln]
    out = {"title": "Build this plan?" if jobs or not section else "Get these for your library?", "sentence": sentence, "lines": lines}
    if section:
        out["knowledge"] = {"topics": list(knowledge["topics"]), "hosts": list(knowledge["hosts"]),
                            "lines": section}
    if searched:
        out["searches"] = {"words": search_words(searches), "lines": searched}
    if offers:
        out["offers"] = offers                     # what she may tick (card_sha covers them)
    downloads = [{"value": o["value"], "line": o["line"], "ticked": o["ticked"]} for o in (knowledge or {}).get("offers") or []]
    if downloads:
        out["knowledge_offers"] = downloads        # the downloads she may tick (D99); card_sha covers them
    tickable = [dict(m, value=f"model:{m['role']}") for m in kept_models if m["fits"]]
    if tickable:
        out["model_offers"] = tickable             # a model she may tick (lane I17); card_sha covers them too
    return out


def model_offer(o: Any) -> dict | None:
    """A model offer as the Core keeps it (lane I17): a role, a catalog model and the Core's own line."""
    from ...zone2 import roles
    if not isinstance(o, dict) or o.get("role") not in roles.OFFERED or not _s(o.get("line"), 300):
        return None
    fits = o.get("fits") is True and bool(_s(o.get("model"), 80))
    return {"role": o["role"], "model": _s(o.get("model"), 80) if fits else "", "label": _s(o.get("label"), 80),
            "disk_gb": o.get("disk_gb") if isinstance(o.get("disk_gb"), (int, float)) else None,
            "installed": o.get("installed") is True, "fits": fits, "line": _s(o.get("line"), 300)}


#: One lock per bot folder, shared by every :class:`Builds` on it in this process (a Toolsmith host made again on the
#: same folder, as after a restart, must not interleave its writes with the old one's). Keyed by the folder's real
#: path, so a junction or link to it is the same lock. Across processes there is no lock here, and nothing in the
#: Core makes one Core per bot folder (lane I17 looked: no lock file or single-instance check exists; two
#: ``puffpai serve`` on the default ports can't both bind 8420, and ``puffpai setup`` uses a Core already running,
#: ``wizard/terminal.py``, but two Cores on other ports and one folder are not stopped).
_FOLDER_LOCKS: dict[str, threading.RLock] = {}
_FOLDER_LOCKS_LOCK = threading.Lock()


def _folder_lock(state: Path) -> threading.RLock:
    key = os.path.normcase(os.path.realpath(str(state)))
    with _FOLDER_LOCKS_LOCK:
        return _FOLDER_LOCKS.setdefault(key, threading.RLock())


class Builds:
    """The Core's build files. One lock per bot folder for every read-modify-write (:attr:`lock`)."""

    def __init__(self, state: Path | str) -> None:
        self.state = Path(state)
        self._lock = _folder_lock(self.state)

    @property
    def lock(self) -> threading.RLock:
        """The bot folder's build lock: hold it to make a check and the writes after it one step."""
        return self._lock

    def dir(self, build_id: str) -> Path:
        return self.state / "builds" / build_id

    def read(self, build_id: str) -> dict | None:
        if not build_id or not _ID.match(str(build_id)):
            return None
        path = self.dir(build_id) / "build.json"
        for attempt in range(10):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # Windows: a read while write() replaces the file fails for a moment; a missing file is None.
                if not path.exists() and attempt:
                    return None
                time.sleep(0.02)
        return None

    def write(self, build: dict) -> None:
        d = self.dir(build["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"build.{secrets.token_hex(4)}.tmp"         # its own name: two writers never share one
        tmp.write_text(json.dumps(build, indent=2, ensure_ascii=False), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, d / "build.json")
                return
            except PermissionError:                   # Windows: a reader holds the file for a moment
                if attempt == 9:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    def all(self) -> list[dict]:
        root = self.state / "builds"
        out = [self.read(p.name) for p in sorted(root.iterdir())] if root.is_dir() else []
        return [b for b in out if b]

    def change(self, build_id: str, fn: Callable[[dict], Any]) -> dict | None:
        with self._lock:
            b = self.read(build_id)
            if not b:
                return None
            fn(b)
            self.write(b)
            return b

    def create(self, goal: str, jobs: Any, choice: dict, left_out: Any = None) -> dict:
        goal = _s(goal, 600)
        build_id = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        jobs_in = []
        for j in jobs if isinstance(jobs, list) else []:
            if isinstance(j, dict):
                jobs_in.append({"name": _s(j.get("name"), 80), "does": _s(j.get("does"), 300),
                                "sample_ask": _s(j.get("sample_ask"), 300),
                                "pasted": _pasted(j.get("pasted"))})
        b = {"id": build_id, "goal": goal, "jobs_in": jobs_in, "created_at": settings_mod.now(),
             "planning": True, "jobs": [], "setup": [], "knowledge": [], "envelope": None, "plan_sha": None,
             "model": {"where": choice["where"], "provider": choice.get("provider"), "model": choice.get("model")},
             "card": None, "consent": {"id": None, "said": ""}, "yes": None, "said": ""}
        left = [_s(x, 80) for x in (left_out if isinstance(left_out, list) else [])[:6] if _s(x, 80)]
        if left:
            b["left_out"] = left                   # D84: said on the plan card, never dropped quietly
        self.write(b)
        return b

    # -- the plan (from Toolsmith's process, checked) -----------------------------

    def set_plan(self, build_id: str, body: dict, choice: dict, company: str,
                 installed: set[str] | None = None, rows: list[dict] | None = None,
                 shop: list[dict] | None = None, models: list[dict] | None = None) -> dict:
        """Keep Toolsmith's plan (checked field by field) and work out the envelope and the card.
        *installed*: the ids an offer may name (an installed app or tool); None keeps no offer. *rows*: the
        Registry's own rows (what each installed tool does, in its own words, for the card)."""
        b = self.read(build_id)
        if b is None:
            raise KeyError(build_id)
        if not b.get("planning") or b.get("plan_sha"):
            raise PermissionError("This build has its plan already.")
        from . import owner_apis
        owner = owner_apis.rows(self.state)        # the APIs she added herself, for their names on the card (D107)
        jobs = []
        for n, j in enumerate((body.get("jobs") or [])[:12] if isinstance(body.get("jobs"), list) else []):
            if not isinstance(j, dict) or not _s(j.get("name"), 80):
                continue
            plan = j.get("plan") if isinstance(j.get("plan"), dict) else {}
            cands = [c for c in (_candidate(x) for x in (plan.get("candidates") or [])[:find.PER_JOB]) if c]
            reuse = plan.get("reuse") if isinstance(plan.get("reuse"), str) else None
            reuse_name = _s(plan.get("reuse_name"), 80) if reuse else ""
            offer = _offer(plan.get("offer"), installed) if not reuse else None
            # A store suggestion only where nothing on her bot was reused or offered (lane I10c).
            in_store = _store_offer(plan.get("store"), shop) if not reuse and not offer else None
            jobs.append({"n": n, "name": _s(j["name"], 80), "does": _s(j.get("does"), 300),
                         "sample_ask": _s(j.get("sample_ask"), 300) or f"What does {_s(j['name'], 80)} show right now?",
                         "pasted": _pasted(j.get("pasted")),
                         "plan": {"reuse": reuse[:40] if reuse else None, "reuse_name": reuse_name or None,
                                  # the tool the brain picked on each reuse ask: why it is reused
                                  "picked": [{"ask": _s(x.get("ask"), 300), "tool": _s(x.get("tool"), 120)}
                                             for x in (plan.get("picked") or [])[:2] if isinstance(x, dict)]
                                  if reuse else [],
                                  "candidates": [] if reuse else cands,
                                  "not_used": [x for x in (_note(y) for y in (plan.get("not_used") or [])[:60]) if x],
                                  "notes": [_s(x, 300) for x in (plan.get("notes") or [])[:5] if isinstance(x, str)],
                                  # the build log's detail (the brain's no, the ask): never on a card
                                  "detail": [_s(x, 300) for x in (plan.get("detail") or [])[:12]
                                             if isinstance(x, str)],
                                  "code": [x for x in (_note(y) for y in (plan.get("code") or [])[:5]) if x],
                                  "look": [] if reuse else look_rows(plan, owner),
                                  **({"offer": offer} if offer else {}),
                                  **({"store": in_store} if in_store else {})}})
        # D85: GitHub's limit, as Toolsmith saw it (a yes/no and a count, nothing else is read).
        gh = [j.get("plan", {}).get("github") for j in (body.get("jobs") or [])[:12]
              if isinstance(body.get("jobs"), list) and isinstance(j, dict) and isinstance(j.get("plan"), dict)]
        gh = [g for g in gh if isinstance(g, dict)]
        limited = {"slowed": any(g.get("slowed") is True for g in gh),
                   "skipped": max([g["skipped"] for g in gh if isinstance(g.get("skipped"), int)
                                   and not isinstance(g.get("skipped"), bool)] or [0])}
        out_jobs, setup = [], {}
        for j in jobs:
            p = j["plan"]
            first = p["candidates"][:1]
            tools = sorted({t for c in first for t in c["programs"]}
                           | ({"docker"} if first and first[0]["tries_in"] == "docker" else set()))
            steps = [s for s in (programs.step_for(t, f"{first[0]['name']} needs it.")
                                 for t in programs.missing(tools)) if s]
            for s in steps:
                setup.setdefault(s["id"], s)
            out_jobs.append({**j, "id": f"{build_id}-{j['n']}", "steps": steps,
                             "tries": [find.tries_line(c) for c in p["candidates"]],
                             "run": {"state": "planned", "line": "", "since": settings_mod.now(), "trying": None,
                                     "tried": [], "lines": [], "result": None}})
        downloads = any(j["plan"]["candidates"] for j in out_jobs)       # a plan that only reuses fetches nothing
        install_hosts = sorted({adopt.project_host(c) for j in out_jobs for c in j["plan"]["candidates"]}
                               | {h for j in out_jobs for c in j["plan"]["candidates"]
                                  for h in adopt.INSTALL_HOSTS.get(c["runtime"], [])})
        plan_hosts = install_hosts
        if choice.get("where") == "cloud" and policy.TRY_FOUND_APPS:
            # A found app must be able to install (D121): each project host and each registry, shown on the card.
            install_hosts = sorted({*install_hosts, *adopt.PROJECT_HOSTS, *(h for hs in adopt.INSTALL_HOSTS.values() for h in hs)})
        envelope = {"goal": b["goal"],
                    "jobs": [{"name": j["name"], "reuse": j["plan"]["reuse"],
                              "offer": (j["plan"].get("offer") or {}).get("package"),
                              "store": (j["plan"].get("store") or {}).get("package"),
                              "candidates": [c["ref"] for c in j["plan"]["candidates"]]} for j in out_jobs],
                    "setup": sorted(setup), "install_hosts": install_hosts}
        if plan_hosts != install_hosts:            # what an app on this plan may keep at run: plan_cover.difference (D121)
            envelope["plan_hosts"] = plan_hosts
        offered = [m for m in (model_offer(x) for x in (models or [])) if m]
        if offered:                                # what a model tick would download (lane I17)
            envelope["models"] = [{"role": m["role"], "model": m["model"]} for m in offered]
        # The knowledge leg (lane I11): Toolsmith sends topics only; sources, hosts and caps are the Core's own.
        raw = body.get("knowledge") if isinstance(body.get("knowledge"), dict) else {}
        kp = knowledge_mod.plan_of(knowledge_mod.clean_topics(raw.get("topics")), finds=b.get("knowledge_finds"),
                                   context=" ".join([str(b["goal"])] + [f"{j['name']} {j['does']}" for j in out_jobs]),
                                   goal=b["goal"])
        asked = raw.get("asked") if isinstance(raw.get("asked"), dict) else None    # counts only (D84)
        knowledge = knowledge_mod.rows(kp)
        if kp["topics"]:
            envelope["knowledge"] = {"topics": kp["topics"], "hosts": kp["hosts"],
                                     "caps": {f["source"]: [f["per_topic"], f["max_items"], f["max_kb"]]
                                              for f in kp["fetch"]}}
            if kp.get("offers"):
                envelope["knowledge"]["offers"] = [{"value": o["value"], "hosts": o["hosts"], "ticked": o["ticked"],
                                                    "bytes": o["bytes"], "address": o.get("meta") or o.get("file") or ""}
                                                   for o in kp["offers"]]

        for j in out_jobs:                         # a tool description kept off her card: said in the build log
            p = j["plan"]
            named = [str(x.get("tool") or "") for x in p.get("picked") or [] if isinstance(x, dict)]                 + [str((p.get("offer") or {}).get("tool") or "")]
            p["detail"] = list(p.get("detail") or []) + find.tool_flags(named, rows) \
                + find.tool_flags([str((p.get("store") or {}).get("tool") or "")], shop)

        def fn(x: dict) -> None:
            x.update(jobs=out_jobs, setup=list(setup.values()), knowledge=knowledge, knowledge_plan=kp,
                     envelope=envelope, plan_sha=sha_of(envelope), planning=False, now="",
                     agreed=agreed_choice(choice),          # what the card says about the model (lane I18e)
                     card=card(x["goal"], out_jobs, list(setup.values()), choice, company, rows, shop, knowledge=kp,
                               searches=x.get("github_searches"), held=x.get("held_names"), models=offered,
                               left_out=x.get("left_out"), limited=limited, asked=asked,
                               by_place=x.get("searches_by_place"), whole=x.get("read_whole"),
                               places=body.get("places"), late=list(x.get("knowledge_down") or []), owner=owner))
            x.pop("held_names", None)              # said once, in the card's own line; kept nowhere else
            x.pop("knowledge_finds", None)         # what was on offer is on the plan now
            if asked is not None:                  # the build log: topics the brain gave vs kept (D84)
                x["knowledge_asked"] = {k: int(v) for k, v in asked.items()
                                        if k in ("jobs", "gave", "no_answer", "timed_out", "kept")
                                        and isinstance(v, int) and not isinstance(v, bool)}
        return self.change(build_id, fn)

    # -- her ticks on the plan card's offers (the Core, from her answer) ------------

    def set_use(self, build_id: str, consent_id: str, use: Any, by: str = "owner",
                pending: Callable[[str], bool] = lambda _cid: False) -> dict:
        """Record which offers she ticked on the plan card (job numbers), before her Yes. Only offers the
        card shows, and only while the card still waits (*pending*, asked under the build's lock; unknown
        counts as not waiting). Returns ``{job id: package}``."""
        items = use if isinstance(use, list) else []
        picked = {int(n) for n in items if (isinstance(n, int) and not isinstance(n, bool))
                  or (isinstance(n, str) and n.isdigit())}
        roles_ticked = {n.split(":", 1)[1] for n in items if isinstance(n, str) and n.startswith("model:")}
        knowledge_ticked = [n for n in items if isinstance(n, str) and n.startswith("knowledge:")]
        out: dict = {}

        def fn(b: dict) -> None:
            if not consent_id or consent_id != (b.get("consent") or {}).get("id"):
                raise PermissionError("That is not this plan's Yes card.")
            if b.get("yes") or b.get("use_fixed") or not pending(consent_id):
                raise PermissionError("This plan's card was answered already, so what it uses can't change.")
            offers = {o["job"]: o for o in ((b.get("card") or {}).get("offers") or []) if isinstance(o, dict)}
            models = {m["role"]: m for m in ((b.get("card") or {}).get("model_offers") or [])
                      if isinstance(m, dict) and m.get("role")}
            downloads = {o["value"] for o in ((b.get("card") or {}).get("knowledge_offers") or []) if isinstance(o, dict)}
            if picked - set(offers) or roles_ticked - set(models) or set(knowledge_ticked) - downloads:
                raise ValueError("Only an offer on the plan card can be ticked.")
            for j in b["jobs"]:
                if j["n"] in picked:
                    out[j["id"]] = offers[j["n"]]["package"]
            b["use"] = {"consent_id": consent_id, "jobs": dict(out), "by": str(by or "owner")[:64],
                        "at": settings_mod.now()}
            if roles_ticked:                       # a model offer she ticked (lane I17): role -> model
                b["use"]["models"] = {r: str(models[r]["model"]) for r in sorted(roles_ticked)}
            if downloads:                          # the downloads left ticked (D99): her list, even when empty
                b["use"]["knowledge"] = sorted(knowledge_ticked)
        if self.change(build_id, fn) is None:
            raise KeyError(build_id)
        return out

    def fix_use(self, build_id: str, consent_id: str) -> None:
        """Her plan card was granted: the ticks it had at that moment are copied into ``use_fixed`` (once).
        ``start`` reads only this copy, so a tick that comes after her Yes never counts."""
        def fn(b: dict) -> None:
            if b.get("use_fixed") or consent_id != (b.get("consent") or {}).get("id"):
                return
            use = b.get("use") if isinstance(b.get("use"), dict) and b["use"].get("consent_id") == consent_id else {}
            b["use_fixed"] = {"consent_id": consent_id, "jobs": dict(use.get("jobs") or {}),
                              "at": settings_mod.now()}
            if use.get("models"):                  # the models she ticked (lane I17)
                b["use_fixed"]["models"] = dict(use["models"])
            if isinstance(use.get("knowledge"), list):    # the downloads she left ticked (D99)
                b["use_fixed"]["knowledge"] = list(use["knowledge"])
        self.change(build_id, fn)

    # -- job status (from Toolsmith's process, checked) -------------------------

    def end_without_yes(self, build_id: str, consent_id: str, answer: str) -> bool:
        """Her plan card ended with no Yes (*answer*: ``declined`` or ``expired``): every job that was waiting
        for it ends with one plain line. Only for this build's own card, and never once a Yes is recorded.
        True when it changed something."""
        state, line = ("said_no", SAID_NO_LINE) if answer == "declined" else ("no_answer", NO_ANSWER_LINE)
        done: dict = {}

        def fn(b: dict) -> None:
            if b.get("yes") or b.get("replaced_by") or not consent_id \
                    or (b.get("consent") or {}).get("id") != consent_id:
                return                                 # a replaced build keeps its own true reason
            b["consent"]["said"] = line
            b["planning"] = False
            for j in b["jobs"]:
                if (j["run"].get("state") or "planned") in ("planned", "waiting_yes"):
                    j["run"].update(state=state, line=line, trying=None, now="", since=settings_mod.now())
                    done["any"] = True
        self.change(build_id, fn)
        return bool(done)

    def end_replaced(self, build_id: str, new_id: str) -> str | None:
        """A newer plan for the same goal (*new_id*) takes this build's place: every job that had not started
        ends with :data:`REPLACED_LINE`, and the build remembers what replaced it (``start`` refuses it then).
        Never once its Yes started it. Returns its plan card's consent id ("" when it had none), or None when
        nothing changed (started already, or replaced before)."""
        out: dict = {}

        def fn(b: dict) -> None:
            if b.get("yes") or b.get("replaced_by"):
                return
            b["replaced_by"] = str(new_id)
            b["planning"] = False
            b["said"] = REPLACED_LINE
            b.setdefault("consent", {"id": None, "said": ""})["said"] = REPLACED_LINE
            for j in b.get("jobs") or []:
                if (j["run"].get("state") or "planned") in ("planned", "waiting_yes"):
                    j["run"].update(state="replaced", line=REPLACED_LINE, trying=None, now="",
                                    since=settings_mod.now())
            out["cid"] = str((b.get("consent") or {}).get("id") or "")
        self.change(build_id, fn)
        return out.get("cid")

    def job_update(self, build_id: str, job_id: str, body: dict) -> dict | None:
        """One status write. Returns the job, or None when there is no such job."""
        if not isinstance(body, dict):
            body = {}
        state = body.get("state")
        if state is not None and state not in STATES:
            raise ValueError("That is not a job state.")
        result = body.get("result")
        if result is not None and (not isinstance(result, dict)
                                   or len(json.dumps(result, ensure_ascii=False)) > MAX_RESULT_BYTES):
            raise ValueError("A job result is a small object.")
        found: dict = {}

        def fn(b: dict) -> None:
            for j in b["jobs"]:
                if j["id"] != job_id:
                    continue
                run = j["run"]
                if state is not None:
                    run.update(state=state, line=_s(body.get("line"), 600), since=settings_mod.now())
                    if "trying" in body:
                        run["trying"] = _s(body.get("trying"), 80) or None
                if "say" in body:
                    run["lines"] = (run["lines"] + [_s(body["say"], 300)])[-60:]
                if isinstance(body.get("needs"), list):       # what it worked out this job needs (D121)
                    run["needs"] = [x for x in (_s(y, 120) for y in body["needs"][:12]) if x]
                if "now" in body:                    # what it does right now: one line, replaced each time
                    run["now"] = _s(body["now"], 300)
                if state is not None and state not in ACTIVE:
                    run["now"] = ""
                if isinstance(body.get("tried"), dict):
                    t = _note(body["tried"])
                    run["tried"] = (run["tried"] + [t])[-40:]
                if isinstance(body.get("not_used"), list):
                    j["plan"]["not_used"] += [x for x in (_note(y) for y in body["not_used"][:40]) if x]
                if result is not None:
                    run["result"] = result
                found.update(j)
        self.change(build_id, fn)
        return found or None

    # -- proof that a package works for a job --------------------------------------

    def proven(self) -> dict:
        try:
            data = json.loads((self.state / "toolsmith" / "proven.json").read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def remember_proven(self, job: dict, pkg_id: str) -> None:
        with self._lock:
            data = self.proven()
            ids = data.setdefault(find.job_key(job), [])
            if pkg_id not in ids:
                ids.append(pkg_id)
            p = self.state / "toolsmith" / "proven.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")


__all__ = ["ACTIVE", "Builds", "EMPTY_PLAN_LINE", "NOTHING_LINE", "STATES", "UNFINISHED", "again_hosts", "agreed_choice",
           "bridge_target", "card", "job_status", "library_job",
           "model_offer", "nothing_line", "sha_of"]
