"""The knowledge leg of a build, on the Core's side (lane I11): what the plan card says, and the fetch itself.

Toolsmith's process only proposes **topics** (one small JSON ask of the local brain, ``toolsmith.knowledge``).
Everything else is the Core's own, from ``toolsmith/data/sources.json``:

- :func:`plan_of`: the topics checked again (short plain words, nothing aimed at an AI, no personal detail
  sent out), the sources fetched with their hosts and caps, the best downloads found (D99: at most 5, tickable,
  over 2 GB starts unticked), what is only to look at, and what could not be searched ("Big download, add later").
  It goes in the plan's envelope, so her ONE plan Yes covers exactly these topics and hosts
  (``covers.knowledge_hosts``).
- :class:`Fetcher`: after her Yes, while Toolsmith's build process runs, one step per call: one search or
  one article or book. Every call is the Core's own send (``Ports.http_send``) under the build's own Bridge
  subject ``pkg:toolsmith-build-<id>`` in the ``build`` phase: the Bridge grants those hosts only from her
  plan Yes and only while this build runs (``Bridge.open_build`` / ``end_build``), and writes each decision
  to ``outside.jsonl``. Addresses are built here from fixed templates; Toolsmith never names one.
- Outside text is untrusted: Drawbridge's text rules (``Ports.screen_knowledge``) stop clearly hostile text
  and hide lines aimed at an AI; the text is kept as a plain file in the data folder (``zone1.fetched``) and
  indexed through the Library's own gate (held values to the vault). It is data for ``search_knowledge``,
  never a prompt.
- Each source has its own small reader (``knowledge_readers``), named by its row in ``sources.json`` (D99).
- Caps: about 40 Wikipedia articles in all (a few per topic) and at most 2 books per topic, 10 in all, each
  read with a size cap. The build's progress lines and its last line say what was read and why a source
  failed, in plain words.
- About the goal, not just the topic word (lane I18d): a page is kept only when its title, Wikipedia's short
  description and its start (a book's first pages) show one of the goal's own words or another topic's
  (:func:`fit_words`); the words only its own topic has never count, since every page it found has them.
  "sky clarity" finds albums called Clarity; they show no other word of a space goal, so they are left out.
  These words never leave this computer. A page left out is replaced by the next one its search found, and
  the done line says how many were left out. Gutenberg (``word_retry``) is asked again with one of a topic's
  own words when the whole phrase finds no book (its search reads a book's title, author and subjects, not
  its text, so a modern two-word phrase rarely finds an old book). Nothing but the topics' own words is sent.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_all
from pathlib import Path
from typing import Any

from ...zone1 import fetched
from ..toolsmith import find
from ..toolsmith.shelves import says
from ..policy import KNOWLEDGE_HOSTS, knowledge_host_ok
from ..toolsmith.errors import NotBuilt, Refused
from . import knowledge_downloads as downloads
from . import knowledge_readers as readers
from . import settings as settings_mod

logger = logging.getLogger(__name__)
MAX_TOPICS = 15
TOPIC_MAX_CHARS = 60
TOPIC_MAX_WORDS = 5
#: One topic word: letters only, or letter groups joined by hyphens ("self-care"). No digits, no other marks.
_WORD = re.compile(r"^[^\W\d_]+(?:-[^\W\d_]+)*$")
#: The allow-list for a word the owner's words make (a search, a topic): plain ASCII letters, joined by single
#: hyphens, after NFKC (lane I15). Any other letter (a dotless i, a small capital, Cherokee, Armenian, Cyrillic or
#: Greek, an accent) is refused; this is stricter than folding lookalikes first.
_ASCII_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
#: Words that end a spelled-out address: "jane at example com", "jane at example dot com".
_TLDS = frozenset("""
com net org edu gov mil int info biz name pro io co me us uk ca au nz de fr es it nl be ch at se no dk fi pl cz
ie pt ru cn jp kr in br mx ar za eu app dev ai xyz online site tech store email mail
""".split())
#: Words that start common key shapes ("sk live ...", "ghp ...", "xoxb ...").
_KEY_WORDS = frozenset("sk pk rk ghp gho ghs ghu xoxb xoxp xoxa xoxr akia asia aiza glpat hf".split())
_VOWELS = frozenset("aeiouy")
#: Said on the plan card right under the topics, so her Yes knows exactly what leaves.
SENT_LINE = ("These words are sent to Wikipedia and Gutenberg as searches (Gutenberg may get a topic one word at a "
             "time).")
#: A download over this size starts unticked (03 "The third leg"), and how many go on the card.
UNTICKED_OVER = 2_000_000_000
BEST_DOWNLOADS = 5
BEST_PER_SOURCE = 4
#: The small words of English and the words that say what a bot should do, never what it is about. They never
#: decide whether a page fits the goal (lane I18d). One list for every goal.
_COMMON = frozenset("""
a about above after again all also am an and any are as at be been before being below between both but by can
could did do does doing down during each else every few for from further get gets give gives go goes going got
had has have having he her here hers him his how i id if in into is it its just keep keeps know knows let lets
like likes make makes many me mine more most much my near new next no nor not now of off often on once one only
or other our ours out over own same she should show shows so some such than that the their theirs them then there
these they this those through to too under until up us very want wants was way we well were what when where which
while who whom why will with would you your yours
anything app apps assistant bot bots build cool everything find finds good great help helps learn lets need needs
please something stuff tell tells thing things track tracks use uses see sees outside inside able best
""".split())
#: How much of a page the goal check reads: an article's start, a book's first pages (its title page, contents
#: and preface).
FIT_CHARS = {"wikipedia": 3000, "gutenberg": 20000}
_WIKI_LIST_MAX = 20                            # the longest search answer asked of Wikipedia
_BIG_STEP_GUARD = 3000                         # more steps than any plan can need: the queue is broken
NO_TOPICS_LINE = "Your bot's brain picked no knowledge topics, so no knowledge is read for this build."


def _cfg() -> dict:
    return find.sources()


def _letters(text: Any) -> list[str]:
    return [w.lower() for w in re.findall(r"[^\W\d_]+", str(text or ""))]


def _fold(text: Any) -> str:
    """*text* as the name check reads it: every hidden or format character dropped (``text_rules.hidden_char``:
    zero-width space, soft hyphen, joiners and the rest), accents and other combining marks dropped after NFKD,
    Cyrillic and Greek letters that look Latin read as Latin (``text_rules.LOOKALIKE``), then NFKC and
    case-folded."""
    from ..text_rules import LOOKALIKE, hidden_char
    text = " ".join(str(text or "").split())
    text = "".join(ch for ch in text if not hidden_char(ch))
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return unicodedata.normalize("NFKC", text.translate(LOOKALIKE)).casefold()


#: A folded word and its hyphen-joined parts ("mari-sol").
_JOINED = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)+")


def _compare_words(text: Any) -> set[str]:
    """What a name is compared with: each folded word and hyphen part, and each run of two or more side-by-side
    hyphen parts with the hyphens removed ("Mari-sol" is compared as "marisol" too, "mari-sol-dog" as "marisol",
    "soldog" and "marisoldog"; "rose-garden" as "rose", "garden" and "rosegarden"). Whole parts only, never
    letters inside a part."""
    folded = _fold(text)
    out = set(_letters(folded))
    for joined in _JOINED.findall(folded):
        parts = joined.split("-")[:12]
        out |= {"".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 2, len(parts) + 1)}
    return out


def name_words(household: Any) -> set[str]:
    """The household's names as folded letter words (2 letters or more), a hyphenated name also joined."""
    return {w for n in (household or []) for w in _compare_words(n) if len(w) >= 2}


def names_a_household_name(text: Any, names: set[str]) -> bool:
    """True when *text*, folded (:func:`_fold`), holds one of *names* (:func:`name_words`) as a whole word or a
    hyphen part ("mark notes", "mark-notes", "mark notes" written with a Cyrillic "a"), or as a hyphen-joined
    word with its hyphens removed ("Mari-sol dog" holds "marisol"). Never inside a longer word:
    "markdown editor" and "bookmark manager" are kept for a household with a Mark. Known limit: a name run into
    another word ("marisolpuppy") is not caught; the plan card lists every search word before the owner's Yes,
    so the owner sees it."""
    return bool(names) and bool(_compare_words(text) & names)


#: Scripts whose letters look alike: a word that mixes two of them is a disguise, never a plain word.
_LOOKALIKE_SCRIPTS = ("LATIN", "CYRILLIC", "GREEK")


def mixed_scripts(word: str) -> bool:
    """True when one word mixes Latin, Cyrillic or Greek letters ("mark" written with a Cyrillic "a")."""
    seen = set()
    for ch in unicodedata.normalize("NFKC", word):
        if ch.isalpha():
            name = unicodedata.name(ch, "")
            seen |= {s for s in _LOOKALIKE_SCRIPTS if name.startswith(s + " ")}
    return len(seen) > 1


def _spelled_address(words: list[str]) -> bool:
    """A word, "at", a word, then "dot" or a top-level-domain word: an email address said in words."""
    low = [w.lower() for w in words]
    return any(low[i] == "at" and i >= 1 and i + 2 < len(low) and (low[i + 2] == "dot" or low[i + 2] in _TLDS)
               for i in range(len(low)))


def _key_shaped(word: str) -> bool:
    """A word that looks like part of a key: a known key prefix, a very long run, mixed case inside a long
    run, or a long run of consonants (random letters)."""
    w = word.replace("-", "")
    if w.lower() in _KEY_WORDS or len(w) >= 20:
        return True
    if len(w) >= 8 and any(c.isupper() for c in w[1:]) and any(c.islower() for c in w):
        return True
    run = 0
    for c in w.lower():
        run = run + 1 if c.isascii() and c.isalpha() and c not in _VOWELS else 0
        if run >= 6:
            return True
    return False


def _style(topics: list[str]) -> str:
    """How the model wrote its topics: ``lower`` (all lower case, as asked), ``sentence`` (first word
    capitalised) or ``title`` (every word capitalised). Capitals mean a name only where the style says so."""
    words = [t.split() for t in topics if t.split()]
    if not words:
        return "lower"
    title = sum(1 for ws in words if len(ws) > 1 and all(w[:1].isupper() for w in ws))
    first = sum(1 for ws in words if ws[0][:1].isupper())
    multi = sum(1 for ws in words if len(ws) > 1) or 1
    if title * 2 > multi:
        return "title"
    if first * 2 > len(words):
        return "sentence"
    return "lower"


def topic_problem(t: str, *, household: set[str] | None = None, context: set[str] | None = None,
                  style: str = "lower") -> str:
    """Why a topic may not be sent out as search words ("" when it may). Shape: letters, spaces and hyphens
    only, 1 to :data:`TOPIC_MAX_WORDS` words, at most :data:`TOPIC_MAX_CHARS` characters. Never: a spelled-out
    email address, a key shape, a name from the household (*household*: lower-case name words), or (with
    *context*, the goal's own words) a word written with a capital that the goal never used, where the model's
    *style* makes a capital mean a name."""
    from ..text_rules import owner_text_flag
    from ..text_rules import has_hidden
    if has_hidden(t):
        return "a hidden character"
    words = t.split()
    if any(mixed_scripts(w) for w in words):
        return "mixed letters"
    if not (2 <= len(t) <= TOPIC_MAX_CHARS) or not 1 <= len(words) <= TOPIC_MAX_WORDS:
        return "shape"
    if not all(_WORD.match(w) for w in words):
        return "not only letters"
    if not all(_ASCII_WORD.fullmatch(unicodedata.normalize("NFKC", w)) for w in words):
        return "not plain letters"
    if owner_text_flag(t):
        return "flagged"
    if _spelled_address(words):
        return "an address"
    if any(_key_shaped(w) for w in words):
        return "a key shape"
    if household and names_a_household_name(t, household):
        return "a household name"
    if context is not None and style != "title":
        for n, w in enumerate(words):
            if w[:1].isupper() and (n > 0 or style == "lower") and w.lower() not in context:
                return "a name"
    return ""


def clean_topics(raw: Any, screen: Any = None, *, household: Any = None, context: Any = None,
                 held: list | None = None) -> list[str]:
    """The topics as the Core keeps them, each once, at most :data:`MAX_TOPICS`; any that
    :func:`topic_problem` names is dropped whole (never sent in part). *household*: names (the people list,
    the owner's own name and what she is called); *context*: the goal and the jobs, her own words. With
    *screen* (the outbound privacy screen) a topic it would change is dropped too; a screen that breaks drops
    them all, since topics are sent to Wikipedia and Gutenberg as search words. *held*: each topic dropped
    because it used a household or You name is added to it, for the owner's own plan card only."""
    names = name_words(household)
    ctx = set(_letters(context)) if context is not None else None
    given = [" ".join(t.split()).strip(" .,:;'-") for t in (raw if isinstance(raw, list) else [])
             if isinstance(t, str)]
    style = _style(given)
    out: list[str] = []
    for t in given:
        why = topic_problem(t, household=names, context=ctx, style=style)
        if why:
            if why == "a household name" and held is not None and t.lower() not in {x.lower() for x in held}:
                held.append(t)
            continue
        if screen is not None:
            try:
                if screen(t) != t:
                    continue                   # a personal detail or a key would be sent: never
            except Exception:  # noqa: BLE001 - no privacy check: nothing is sent
                return []
        if t.lower() not in {x.lower() for x in out}:
            out.append(t)
        if len(out) >= MAX_TOPICS:
            break
    return out


# -- does a page fit the goal? (lane I18d) ---------------------------------------------------------------------

def _stem(word: str) -> str:
    """A plain word as the goal check compares it: plural, "-ing" and "-er" endings off, then its first 7
    letters ("telescopes" and "telescope", "planning" and "planner", "astronomy" and "astronomers")."""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for end in ("ing", "er"):
        if word.endswith(end) and len(word) - len(end) >= 4:
            word = word[:-len(end)]
            break
    return word[:7]


def subject_words(text: Any) -> set[str]:
    """The words of *text* that can say what it is about: 3 letters or more, not in :data:`_COMMON`, as stems."""
    return {_stem(w) for w in _letters(_fold(text)) if len(w) >= 3 and w not in _COMMON}


def fit_words(goal: Any, topics: list[str]) -> dict[str, frozenset[str]]:
    """Per topic, the words a page it finds must show at least one of: the goal's own words and the other
    topics' words, never the words only this topic has (every page found by "sky clarity" has "sky" or
    "clarity"; an album called Clarity has neither "space" nor "astronomy"). A word the goal shares with the
    topic counts ("chess" in "chess openings" for a chess goal). An empty set means nothing to judge by: every
    page is kept. These words never leave this computer."""
    goal_w = subject_words(goal)
    per = {t: subject_words(t) for t in topics}
    out = {}
    for t in per:
        others = set(goal_w)
        for u, words in per.items():
            if u != t:
                others |= words
        out[t] = frozenset(others)              # this topic's words count only where the goal or another has them
    return out


#: A goal with this many words of its own (with its topics') is told apart by two of them on a page, not one:
#: one word alone ("space" in "open space") is often another meaning.
RICH_GOAL = 6


def fits_goal(text: Any, need: frozenset[str] | set[str]) -> bool:
    """True when *text* (a page's title, description and start) shows one of *need* (two different ones when
    *need* has :data:`RICH_GOAL` words or more), or *need* is empty."""
    if not need:
        return True
    return len(subject_words(text) & set(need)) >= (2 if len(need) >= RICH_GOAL else 1)


def per_topic(src: dict, n_topics: int) -> int:
    """How many items one topic may bring: the source's cap shared over the topics, within its limits."""
    share = math.ceil(int(src["max_items"]) / max(1, n_topics))
    return max(int(src["per_topic_min"]), min(int(src["per_topic_max"]), share))


def _hosts_of(row: dict, item: dict) -> list[str]:
    """The hosts one download reads: Kiwix's .meta4 host and its one mirror, or a dataset's own file host."""
    if row.get("reader") == "kiwix":
        return [row["meta_host"], *row["download_hosts"]]
    return [urllib.parse.urlsplit(str(item.get("file") or "")).hostname or ""]


def safe_license(value: Any, own: str = "") -> str:
    """A license a place's answer names, as her card may show it: a plain http(s) address, or a short string the owner
    screen does not flag. Anything else: *own*, the row's own words."""
    text = " ".join(str(value or "").split())
    if _LICENSE_URL.fullmatch(text):
        return text
    return text if text and len(text) <= LICENSE_MAX and _safe_title(text, "") == text else own


def _offer(sid: str, row: dict, item: dict) -> dict | None:
    hosts = _hosts_of(row, item)
    if not all(h and knowledge_host_ok(h) for h in hosts):
        return None                            # only hosts the Bridge may grant from a plan Yes
    size = int(item.get("bytes") or 0)
    title = _safe_title(item.get("title"), f"A download from {row['short']}")
    licence = safe_license(item.get("license"), row["license"])
    said = downloads.size_said(size) if size else f"size not known, cut at {int(row.get('download_max_mb') or 0)} MB"
    ticked = not size or size <= UNTICKED_OVER
    line = (f"Download {title} ({said}) from {row['short']}, reading {' and '.join(hosts)}. License: {licence}. "
            f"{row['credit']}" + ("" if ticked else " Over 2 GB, so it starts unticked."))
    return {"value": f"knowledge:{sid}:{item['id']}", "source": sid, "id": item["id"], "title": title, "hosts": hosts,
            "bytes": size, "ticked": ticked, "license": licence, "line": line, "summary": str(item.get("summary") or ""),
            "articles": int(item.get("articles") or 0), "publisher": str(item.get("publisher") or ""),
            **{k: str(item[k]) for k in ("meta", "file", "format", "page") if item.get(k)}}


def best_offers(finds: dict, topics: list[str], goal: Any) -> list[dict]:
    """The best downloads found: about the goal or the topics (:func:`fits_goal`), most of those words first, the
    smaller first, at most :data:`BEST_DOWNLOADS`, at most :data:`BEST_PER_SOURCE` from one place."""
    cfg = _cfg().get("catalogs") or {}
    need = subject_words(goal) | {w for t in topics for w in subject_words(t)}
    rows = []
    for sid, items in (finds or {}).items():
        row = cfg.get(sid)
        if not isinstance(row, dict) or row.get("look_only") or not isinstance(items, list):
            continue
        for item in items:
            text = f"{item.get('title') or ''}\n{item.get('summary') or ''}"
            if isinstance(item, dict) and item.get("id") and fits_goal(text, need):
                rows.append((-len(subject_words(text) & need), int(item.get("bytes") or 0), len(rows), sid, row, item))
    out: list[dict] = []
    per: dict[str, int] = {}
    for _w, _b, _n, sid, row, item in sorted(rows, key=lambda r: r[:3]):
        offer = _offer(sid, row, item) if per.get(sid, 0) < BEST_PER_SOURCE else None
        if offer and offer["value"] not in {o["value"] for o in out}:
            per[sid] = per.get(sid, 0) + 1
            out.append(offer)
        if len(out) >= BEST_DOWNLOADS:
            break
    return out


def look_rows(finds: dict, topics: list[str], goal: Any) -> list[dict]:
    """What a look-only catalog found: named on the card, never ticked, never fetched."""
    from ..text_rules import url_is_plain
    cfg = _cfg().get("catalogs") or {}
    need = subject_words(goal) | {w for t in topics for w in subject_words(t)}
    out = []
    for sid, items in (finds or {}).items():
        row = cfg.get(sid) if isinstance(cfg.get(sid), dict) else {}
        for item in (items if row.get("look_only") and isinstance(items, list) else [])[:40]:
            text = f"{item.get('title') or ''}\n{item.get('summary') or ''}"
            title = _safe_title(item.get("title"), "")
            if title and url_is_plain(item.get("page")) and fits_goal(text, need) and len(out) < BEST_DOWNLOADS:
                out.append({"source": sid, "title": title, "url": str(item["page"]), "short": row["short"],
                            "license": safe_license(item.get("license"), str(row.get("license") or ""))})
    return out


def plan_of(topics: list[str], finds: dict | None = None, context: Any = "", goal: Any = "") -> dict:
    """The knowledge part of a plan (what her Yes covers): topics, sources fetched, sources offered, hosts. *finds*:
    what the catalogs found while planning (:meth:`Fetcher.find`); *context*: the goal and the jobs, for a source
    that fits only some goals (``only_when``); these words never leave this computer."""
    cfg = _cfg()
    if not topics:
        return {"topics": [], "fetch": [], "offered": [], "hosts": []}
    said = " ".join([str(context or "")] + list(topics))
    fetch = []
    for sid, s in cfg["fetch"].items():
        if sid not in fetched.SOURCES or s.get("reader") not in READERS or s.get("host") not in KNOWLEDGE_HOSTS:
            continue                           # only the sources this code knows how to read, at their hosts
        if s.get("only_when") and not says(said, s["only_when"]):
            continue                           # a source for some goals only: this goal's words never reach it
        fetch.append({"source": sid, "name": s["name"], "host": s["host"], "kind": s["kind"],
                      "per_topic": per_topic(s, len(topics)), "max_items": int(s["max_items"]),
                      "max_kb": int(s["max_bytes"]) // 1000, "about_mb": _about_mb(s, len(topics)),
                      "license": s["license"], **({"file_hosts": list(s["file_hosts"])} if s.get("file_hosts") else {})})
    q = urllib.parse.quote(topics[0])
    finds = finds if isinstance(finds, dict) else {}
    offered = [{"source": oid, "title": o["title"], "host": o["host"], "size": o["size"], "kind": o["kind"],
                "look": o["look"].format(q=q)} for oid, o in cfg["offered"].items() if oid not in finds]
    offers, looks = best_offers(finds, topics, goal or context), look_rows(finds, topics, goal or context)
    hosts = {f["host"] for f in fetch} | {h for f in fetch for h in f.get("file_hosts", [])} \
        | {h for o in offers for h in o["hosts"]}
    out = {"topics": list(topics), "fetch": fetch, "offered": offered, "hosts": sorted(hosts)}
    if offers:
        out["offers"] = offers
    if looks:
        out["look"] = looks
    return out


def approved(kp: dict, sid: Any) -> dict | None:
    """A source as her Yes approved it: today's addresses from ``sources.json``, but never more than the plan
    card said (its host, its caps per topic and in all, its size cap). None: not on her plan."""
    on_plan = next((f for f in (kp or {}).get("fetch") or [] if f.get("source") == sid), None)
    src = _cfg()["fetch"].get(sid) if on_plan else None
    if src is None or src.get("host") != on_plan.get("host"):
        return None
    src = dict(src)
    src["max_items"] = min(int(src["max_items"]), int(on_plan["max_items"]))
    src["per_topic_max"] = min(int(src["per_topic_max"]), int(on_plan["per_topic"]))
    src["per_topic_min"] = min(int(src["per_topic_min"]), src["per_topic_max"])
    src["max_bytes"] = min(int(src["max_bytes"]), int(on_plan["max_kb"]) * 1000)
    return src


def _about_mb(s: dict, n_topics: int) -> float:
    items = min(int(s["max_items"]), per_topic(s, n_topics) * n_topics)
    return round(items * int(s["about_each_kb"]) / 1000, 1)


def sent_line(kp: dict) -> str:
    """Her line under the topics: every place the topics go after her Yes, by name."""
    cfg = _cfg()["fetch"]
    names = [str((cfg.get(f["source"]) or {}).get("short") or f["name"]) for f in kp.get("fetch") or []]
    if names in ([], ["Wikipedia", "Gutenberg"]):
        return SENT_LINE
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    return f"These words are sent to {said} as searches" + \
        (" (Gutenberg may get a topic one word at a time)." if "Gutenberg" in names else ".")


def card_lines(kp: dict) -> list[str]:
    """The plan card's Knowledge section: what is fetched, from where, roughly how big; what is only offered."""
    if not kp or not kp.get("topics"):
        return []
    lines = ["Knowledge: Toolsmith reads about " + ", ".join(kp["topics"]) + ".", sent_line(kp)]
    for f in kp["fetch"]:
        if f["source"] not in ("wikipedia", "gutenberg"):
            row = _cfg()["fetch"].get(f["source"]) or {}
            lines.append(f"Knowledge from {f['name']} ({', '.join([f['host']] + f.get('file_hosts', []))}): up to "
                         f"{_plural(f['per_topic'], _one_kind(f['source']), f['kind'])} per topic, {f['max_items']} in all"
                         + (f" (about {f['about_mb']:g} MB). " if f["about_mb"] >= 0.1 else " (under 0.1 MB). ") +
                         f"License: {f['license']}. {row.get('credit') or ''}".strip())
        elif f["source"] == "wikipedia":
            lines.append(f"Knowledge from {f['name']} ({f['host']}): up to {_plural(f['per_topic'], 'article', 'articles')} per topic, "
                         f"{f['max_items']} in all, as plain text (about {f['about_mb']:g} MB). "
                         f"License: {f['license']}, credited to its authors.")
        else:
            lines.append(f"Knowledge from {f['name']} ({f['host']}): up to "
                         f"{_plural(f['per_topic'], 'public domain book', 'public domain books')} per topic, {f['max_items']} in all, each cut at {f['max_kb'] / 1000:g} MB "
                         f"(about {f['about_mb']:g} MB in all).")
    for o in kp.get("offers") or []:
        lines.append(o["line"])
    for o in kp.get("look") or []:
        lines.append(f"To look at, Toolsmith does not download it: {o['title']}, on {o['short']} ({o['url']})"
                     + (f". License: {o['license']}." if o["license"] else "."))
    for o in kp["offered"]:
        lines.append(f"Big download, add later: {o['title']}, from {o['host']}. Size: {o['size']}. "
                     "Nothing is fetched from it now.")
    lines.append("What it reads is kept on this computer as your bot's knowledge, for its chat to search.")
    return lines


def rows(kp: dict, run: dict | None = None) -> list[dict]:
    """``build.knowledge``: one row per source, for the Build page (``{source, title, kind, look, status}``)."""
    out = []
    run = run or {}
    for f in (kp or {}).get("fetch") or []:
        n = int((run.get("counts") or {}).get(f["source"]) or 0)
        failed = (run.get("failed") or {}).get(f["source"])
        if run.get("state") == "done":
            status = failed or f"read {n} {f['kind'] if n != 1 else f['kind'][:-1]}"
        elif run.get("state") == "fetching":
            status = f"reading ({n} so far)"
        else:
            status = f"read after your Yes: up to {f['max_items']} {f['kind']}"
        out.append({"job": 0, "source": f["source"], "title": f"{f['name']} ({f['host']})", "kind": f["kind"],
                    "look": "", "status": status})
    done = (run.get("downloads") or {})
    for o in (kp or {}).get("offers") or []:
        state = done.get(o["value"]) if isinstance(done.get(o["value"]), dict) else {}
        out.append({"job": 0, "source": o["source"], "title": o["title"], "kind": "download", "look": o.get("page", ""),
                    "status": str(state.get("line") or ("ticked, downloads after your Yes" if o["ticked"]
                                                        else "not ticked, over 2 GB"))})
    for o in (kp or {}).get("look") or []:
        out.append({"job": 0, "source": o["source"], "title": o["title"], "kind": "look", "look": o["url"],
                    "status": "only to look at, not downloaded"})
    for o in (kp or {}).get("offered") or []:
        out.append({"job": 0, "source": o["source"], "title": o["title"], "kind": o["kind"], "look": o["look"],
                    "status": f"offered, big download, add later ({o['size']})"})
    return out


def progress(b: dict) -> dict | None:
    """The build's library fetch for Home, Building and Knowledge (``GET /v1/toolsmith``, lane I19b), or None
    when its plan reads nothing: ``{state: waiting|fetching|done, done, of, up_to, sources, now, line}``.
    *done* is what is in the library now; *of* is what the searches found to read, or (``up_to``) the plan's
    cap while a search is still to run. Counts and names only."""
    kp = b.get("knowledge_plan") or {}
    fetch = [f for f in kp.get("fetch") or [] if isinstance(f, dict) and f.get("source")]
    if not kp.get("topics") or not fetch:
        return None
    run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
    counts, planned = run.get("counts") or {}, run.get("planned") or {}
    done = sum(int(counts.get(f["source"]) or 0) for f in fetch)
    found = sum(int(planned.get(f["source"]) or 0) for f in fetch)
    cap = sum(int(f.get("max_items") or 0) for f in fetch)
    state = {"fetching": "fetching", "done": "done"}.get(str(run.get("state") or ""), "waiting")
    searching = state == "waiting" or any(isinstance(u, dict) and u.get("do") == "search" for u in run.get("queue") or [])
    up_to = state != "done" and searching
    return {"state": state, "done": done, "of": cap if up_to else max(found, done), "up_to": up_to,
            "sources": [str(f.get("name") or f["source"]) for f in fetch], "now": str(run.get("now") or ""),
            "line": str(run.get("line") or "")}

# -- the fetch, one step at a time ---------------------------------------------------------------------------

def ticked_of(b: dict) -> list[str] | None:
    """The downloads she left ticked when she said Yes (``use_fixed``), or None when no ticks were sent: then the
    card's own ticks stand."""
    fixed = b.get("use_fixed") if isinstance(b.get("use_fixed"), dict) else {}
    got = fixed.get("knowledge")
    return [str(v) for v in got] if isinstance(got, list) else None


def start_run(kp: dict, ticked: list[str] | None = None) -> dict:
    """A new fetch for this plan: one search per source and topic, in order, then the ticked downloads, one at a
    time."""
    queue = [{"do": "search", "source": f["source"], "topic": t, "n": i + 1}
             for f in kp["fetch"] for i, t in enumerate(kp["topics"])]
    queue += [{"do": "download", "value": o["value"], "source": o["source"], "stage": "start"}
              for o in kp.get("offers") or [] if (o["ticked"] if ticked is None else o["value"] in ticked)]
    return {"state": "fetching", "queue": queue, "counts": {f["source"]: 0 for f in kp["fetch"]},
            "planned": {f["source"]: 0 for f in kp["fetch"]}, "seen": [], "failed": {}, "nothing": {},
            "kept_out": {}, "notes": [], "lines": [], "now": "", "bytes": 0, "steps": 0, "items": [],
            "started_at": settings_mod.now(), "line": ""}


def _one_kind(sid: str) -> str:
    return str((_cfg()["fetch"].get(sid) or {}).get("kind") or "items").rstrip("s")


def finish(run: dict, stopped: bool = False) -> None:
    """End a fetch: its notes (why a source gave nothing), its last line."""
    notes = []
    for line in (run.get("failed") or {}).values():
        notes.append(line.rstrip(".") + ".")
    for sid, topics in (run.get("nothing") or {}).items():
        if topics and sid not in (run.get("failed") or {}):
            notes.append(f"{_cfg()['fetch'][sid]['short']} had nothing on {_or_list(topics)}.")
    for sid, topics in (run.get("unfit") or {}).items():
        if topics and sid not in (run.get("failed") or {}):
            notes.append(f"{_cfg()['fetch'][sid]['short']} had nothing about your goal on {_or_list(topics)}.")
    for sid, n in (run.get("off_goal") or {}).items():
        if n:
            short = _cfg()["fetch"][sid]["short"]
            kind = _one_kind(sid)
            notes.append(f"Left out {_plural(n, short + ' ' + kind, short + ' ' + kind + 's')} that "
                         f"{'was' if n == 1 else 'were'} not about your goal.")
    for sid, n in (run.get("kept_out") or {}).items():
        if n:
            short = _cfg()["fetch"][sid]["short"]
            kind = _one_kind(sid)
            why = str((run.get("kept_why") or {}).get(sid) or "its text could not be checked").rstrip(".")
            notes.append(f"Drawbridge kept {_plural(n, short + ' ' + kind, short + ' ' + kind + 's')} out: {why}.")
    for d in (run.get("downloads") or {}).values():
        if isinstance(d, dict) and d.get("failed"):
            notes.append(str(d["line"]).rstrip(".") + ".")
    if stopped:
        notes.append("Toolsmith stopped before it read everything planned.")
    run["notes"] = notes
    run["state"] = "done"
    run["queue"] = []
    run["now"] = ""
    run["line"] = summary(run)
    run["lines"] = (run["lines"] + [run["line"]])[-40:]
    run["ended_at"] = settings_mod.now()


def end_unfinished(b: dict) -> bool:
    """Toolsmith's build process ended: a fetch it did not finish (or never began) ends with a plain line.
    True when it changed *b*."""
    kp = b.get("knowledge_plan") or {}
    if not kp.get("topics") or not b.get("yes"):
        return False
    run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else start_run(kp, ticked_of(b))
    if run.get("state") != "fetching":
        return False
    finish(run, stopped=True)
    b["knowledge_run"] = run
    b["knowledge"] = rows(kp, run)
    return True


class SourceDown(Exception):
    """A source could not be reached (the Bridge, the network, its own answer): its line, in plain words.
    ``detail``: what the network said, for the build log only."""
    detail = ""


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _or_list(items: list[str]) -> str:
    quoted = list(dict.fromkeys(items))
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]


def shown_title(title: str, what: str) -> str:
    """A title as she may read it in a progress line, or a neutral word when the screen flags it."""
    from ..text_rules import owner_text_flag
    t = " ".join(str(title or "").split())[:120]
    return f"\"{t}\"" if t and not owner_text_flag(t) else what


#: A license named by a place's answer: an address with nothing that could start a new sentence on her card.
_LICENSE_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#@!$&'()*+,;=%\[\]-]{1,190}")
LICENSE_MAX = 60


def _safe_title(title: Any, neutral: str) -> str:
    """A title kept in ``meta.json`` and shown on the Knowledge page: its own words, or *neutral* when the screen
    for owner text flags them (words aimed at an AI, a link, markup, hidden characters)."""
    from ..text_rules import owner_text_flag
    t = " ".join(str(title or "").split())[:200]
    return t if t and not owner_text_flag(t) else neutral


def shown_look(name: Any) -> str:
    """A row-to-look-at's name as the plan card may show it: its own words, or "" when the owner screen flags them
    (words aimed at an AI, a link, markup, hidden characters)."""
    return _safe_title(name, "")[:100]


def summary(run: dict) -> str:
    """The last line: what was read, then why a source gave nothing (lane I11, honest lines)."""
    counts = run.get("counts") or {}
    parts = []
    if counts.get("wikipedia"):
        parts.append(_plural(counts["wikipedia"], "Wikipedia article", "Wikipedia articles"))
    if counts.get("gutenberg"):
        parts.append(_plural(counts["gutenberg"], "public domain book", "public domain books"))
    for sid, n in counts.items():
        row = _cfg()["fetch"].get(sid)
        if n and row and sid not in ("wikipedia", "gutenberg"):
            parts.append(_plural(n, f"{row['short']} {_one_kind(sid)}", f"{row['short']} {row['kind']}"))
    done = [d for d in (run.get("downloads") or {}).values() if isinstance(d, dict) and d.get("done")]
    if done:
        parts.append(_plural(len(done), "download", "downloads"))
    line = ("Read " + " and ".join(parts) + " into your bot's knowledge." if parts
            else "No knowledge was read into your bot.")
    return " ".join([line] + list(run.get("notes") or []))


class Fetcher:
    """The Core's knowledge fetch for builds, one step per call from Toolsmith's build process."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, build_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(build_id, threading.Lock())

    # -- the state, in build.json -------------------------------------------------------------------------
    def _save(self, build_id: str, run: dict, kp: dict) -> None:
        def fn(b: dict) -> None:
            b["knowledge_run"] = run
            b["knowledge"] = rows(kp, run)
        self.host.store.change(build_id, fn)

    def step(self, build_id: str) -> dict:
        """One unit of work: ``{done, now, line, stop?}``. Once done, every call says done with its last line."""
        with self._lock(build_id):
            b = self.host.store.read(build_id)
            if b is None:
                raise KeyError(build_id)
            kp = b.get("knowledge_plan") or {}
            if not kp.get("topics"):
                return {"done": True, "line": ""}
            run = b.get("knowledge_run") or start_run(kp, ticked_of(b))
            if run.get("state") != "fetching":
                return {"done": True, "line": run.get("line") or ""}
            if getattr(self.host, "_stopping", False):
                finish(run, stopped=True)
                self._save(build_id, run, kp)
                return {"done": True, "stop": True, "line": run["line"]}
            run["steps"] = int(run.get("steps") or 0) + 1
            if not run["queue"] or run["steps"] > _BIG_STEP_GUARD:
                finish(run)
                self._save(build_id, run, kp)
                return {"done": True, "line": run["line"]}
            unit = run["queue"].pop(0)
            if unit.get("do") == "download":
                self._download(build_id, b, run, kp, unit)
                self._save(build_id, run, kp)
                return {"done": False, "now": run.get("now") or ""}
            src = approved(kp, unit.get("source"))
            if src is None or unit["source"] in run["failed"]:
                self._save(build_id, run, kp)
                return {"done": False, "now": run.get("now") or ""}
            try:
                if unit["do"] == "search":
                    self._search(build_id, run, kp, src, unit)
                else:
                    need = fit_words(b.get("goal"), list(kp["topics"])).get(unit.get("topic"), frozenset())
                    self._get(build_id, run, src, unit, need)
            except Exception as exc:  # noqa: BLE001 - one source's problem never ends the others
                line = str(exc) if isinstance(exc, (SourceDown, fetched.StoreAway)) else \
                    f"{src['short']} could not be read (the Core hit {type(exc).__name__})"
                run["failed"][unit["source"]] = line
                detail = getattr(exc, "detail", "") or ("" if isinstance(exc, SourceDown) else repr(exc)[:200])
                if detail:                         # the build log's detail, never her line
                    run.setdefault("detail", {})[unit["source"]] = detail
                run["queue"] = [u for u in run["queue"] if u.get("source") != unit["source"]]
                run["lines"] = (run["lines"] + [line])[-40:]
            self._save(build_id, run, kp)
            return {"done": False, "now": run.get("now") or ""}

    # -- the network: the Core's own send, under this build's Bridge subject ------------------------------
    def _fetch(self, build_id: str, src: dict, url: str, *, accept: str, max_bytes: int, headers: dict | None = None,
               timeout: float = 30.0) -> tuple[int, bytes]:
        from .ports import KNOWLEDGE_USER_AGENT
        from .store import bridge_target
        try:
            status, _h, body = self.host.ports.http_send(
                "GET", url, subject=f"pkg:{bridge_target(build_id)}",
                headers={"Accept": accept, "User-Agent": KNOWLEDGE_USER_AGENT, **(headers or {})},
                max_bytes=max_bytes, timeout=timeout, phase="build")
        except Refused as exc:
            said = " ".join(str(exc).split()).lower()
            if "bridge is closed" in said:
                raise SourceDown(f"{src['short']} could not be reached: the Bridge is closed, so nothing was sent")
            if "needs your yes" in said:
                raise SourceDown(f"{src['short']} could not be reached: the Bridge needs your Yes first, so "
                                 "nothing was sent")
            if "redirect" in said:
                raise SourceDown(f"{src['short']} could not be reached: it answered with a redirect, so nothing "
                                 "more was sent")
            raise SourceDown(f"{src['short']} could not be reached") from None
        except OSError as exc:
            down = SourceDown(f"{src['short']} could not be reached")
            reason = getattr(exc, "reason", None) or exc
            down.detail = f"{type(exc).__name__}: {type(reason).__name__} {str(reason)[:120]}".strip()
            raise down from None
        status = int(status or 0)
        if status == 403:
            raise SourceDown(f"{src['short']} refused the request (it answered 403), so nothing was read from it")
        if status == 429 or status >= 500:
            raise SourceDown(f"{src['short']} could not be reached (it answered {status})")
        return status, bytes(body or b"")

    # -- one search ---------------------------------------------------------------------------------------
    def _room(self, run: dict, sid: str, src: dict) -> int:
        return int(src["max_items"]) - int(run["planned"].get(sid) or 0)

    def _search(self, build_id: str, run: dict, kp: dict, src: dict, unit: dict) -> None:
        sid, topic = unit["source"], unit["topic"]
        query = str(unit.get("q") or topic)        # the topic, or one of its own words (never another word)
        n_topics = len(kp["topics"])
        take = min(per_topic(src, n_topics), self._room(run, sid, src))
        if take <= 0:
            return
        run.setdefault("asked", []).append(f"{sid}:{query.lower()}")
        q = urllib.parse.quote(query, safe="")
        said = f"\"{topic}\" (topic {unit['n']} of {n_topics})" if query == topic else \
            f"\"{query}\" (one word of \"{topic}\", topic {unit['n']} of {n_topics})"
        run["now"] = f"Looking in {src['name']} for {said}"
        self._now(build_id, run)
        n = min(_WIKI_LIST_MAX, take * 2 + 3)           # a few more than it reads: spares for pages left out
        reader = READERS[src["reader"]]
        status, body = self._fetch(build_id, src, src["search"].format(q=q, n=n), accept=reader["accept"],
                                   max_bytes=500_000)
        found = reader["found"](body, src) if status == 200 else []
        seen = set(run["seen"])
        picked, spare = [], []
        for key, title, *more in found:
            mark = f"{sid}:{str(key).lower()}"
            if mark in seen or any(s["key"] == key for s in spare):
                continue
            item = {"do": "get", "source": sid, "key": key, "title": title, "topic": topic, "n": unit["n"],
                    **({"more": more[0]} if more else {})}
            if len(picked) < take:
                seen.add(mark)
                picked.append(item)
            elif len(spare) < take + 2:
                spare.append(item)                 # read in place of one that is not about the goal
            else:
                break
        run["seen"] = sorted(seen)[-2000:]
        spares = run.setdefault("spare", {})
        spares[f"{sid}|{topic}"] = spare + list(spares.get(f"{sid}|{topic}") or [])
        if not picked:
            if not self._one_word(run, src, sid, topic, unit["n"]):
                run["nothing"].setdefault(sid, []).append(topic)
            return
        run["planned"][sid] = int(run["planned"].get(sid) or 0) + len(picked)
        run["queue"] = picked + run["queue"]

    def _one_word(self, run: dict, src: dict, sid: str, topic: str, n: int) -> bool:
        """A source that finds nothing for a topic's whole phrase (``word_retry`` in ``sources.json``: old books
        rarely carry a modern phrase) is asked again with one of the topic's own words, longest first, at most
        ``word_retry`` times. Only words of the topic, as the plan card listed them. True when a search was
        queued."""
        tries = int(src.get("word_retry") or 0)
        if tries <= 0 or int((run.get("off_goal") or {}).get(sid) or 0) >= int(src["max_items"]):
            return False
        left = run.setdefault("words", {})
        key = f"{sid}|{topic}"
        if key not in left:
            words = [w for w in dict.fromkeys(topic.lower().split()) if len(w) >= 3 and w not in _COMMON]
            left[key] = sorted(words, key=len, reverse=True)[:tries] if len(words) > 1 else []
        asked = set(run.get("asked") or [])
        while left[key]:
            word = left[key].pop(0)
            if f"{sid}:{word}" not in asked:
                run["queue"].insert(0, {"do": "search", "source": sid, "topic": topic, "n": n, "q": word})
                return True
        return False

    def _not_kept(self, run: dict, src: dict, unit: dict, *, off_goal: bool) -> None:
        """One found page gave nothing (not about the goal, missing, a page of meanings, read already): the next
        one that search found is read in its place. With none left and nothing kept for its topic, a source with
        ``word_retry`` asks again with one word of the topic, else her done line says so."""
        sid, topic = unit["source"], unit.get("topic") or ""
        key = f"{sid}|{topic}"
        if off_goal:
            run.setdefault("off_goal", {})[sid] = int(run.get("off_goal", {}).get(sid) or 0) + 1
        spares = run.setdefault("spare", {}).get(key) or []
        if int(run.get("off_goal", {}).get(sid) or 0) >= int(src["max_items"]):
            spares.clear()                         # never more pages read and left out than the source's cap
        while spares:
            nxt = spares.pop(0)
            mark = f"{sid}:{str(nxt['key']).lower()}"
            if mark not in run["seen"]:
                run["seen"] = (run["seen"] + [mark])[-2000:]
                run["queue"].insert(0, nxt)
                return
        run["planned"][sid] = max(0, int(run["planned"].get(sid) or 0) - 1)
        pending = any(u.get("source") == sid and u.get("topic") == topic for u in run["queue"])
        if pending or int(run.setdefault("got", {}).get(key) or 0):
            return
        if not self._one_word(run, src, sid, topic, int(unit.get("n") or 0)) and off_goal:
            topics = run.setdefault("unfit", {}).setdefault(sid, [])
            if topic not in topics:
                topics.append(topic)

    # -- one article or book ------------------------------------------------------------------------------
    def _get(self, build_id: str, run: dict, src: dict, unit: dict, need: frozenset[str] = frozenset()) -> None:
        sid = unit["source"]
        k = int(run["counts"].get(sid) or 0) + 1
        cap = int(src["max_bytes"])
        keep_file: tuple[str, bytes] | None = None
        # A place that answers any word with a page on something else is read closer to its title (live, 10-30: iFixit
        # answered "invoice" with iMac repair guides).
        fit = int(src.get("fit_chars") or FIT_CHARS.get(sid, 3000))
        if src["reader"] == "wiki":
            run["now"] = (f"Reading the {src['short']} article {shown_title(unit['title'], 'an article')} "
                          f"({k} of at most {src['max_items']})")
            self._now(build_id, run)
            status, body = self._fetch(build_id, src, src["text"].format(title=urllib.parse.quote(
                str(unit["key"]), safe="")), accept="application/json", max_bytes=cap * 4)
            page = _wiki_page(body, src["base"]) if status == 200 else None
            if page is None:
                return self._not_kept(run, src, unit, off_goal=False)   # missing, a page of meanings, or empty
            title, text, url, revision, pageid, about = page
            mark = f"{sid}:page:{pageid}"
            if mark in run["seen"]:
                return self._not_kept(run, src, unit, off_goal=False)   # two search titles for one article
            run["seen"] = (run["seen"] + [mark])[-2000:]
            if not fits_goal(f"{title}\n{about}\n{text[:fit]}", need):
                return self._not_kept(run, src, unit, off_goal=True)
            title = _safe_title(title, f"A {src['short']} article")
            name = f"{fetched.slug(title)[:80]}-{pageid}"
            meta = {"url": url, "revision": revision, "license": src["license"], "license_url": src["license_url"],
                    "credit": src["credit"]}
            head = f"{title}\n\nFrom {src['short']}, {url} (revision {revision}). {src['credit']}\n\n"
        elif src["reader"] == "ifixit":
            run["now"] = f"Reading the iFixit guide {shown_title(unit['title'], 'a guide')} ({k} of at most {src['max_items']})"
            self._now(build_id, run)
            status, body = self._fetch(build_id, src, src["text"].format(id=int(unit["key"])), accept="application/json",
                                       max_bytes=cap * 4)
            text = readers.ifixit_guide(body) if status == 200 else None
            if not text:
                return self._not_kept(run, src, unit, off_goal=False)
            if not fits_goal(f"{unit['title']}\n{text[:fit]}", need):
                return self._not_kept(run, src, unit, off_goal=True)
            title = _safe_title(unit["title"], "An iFixit guide")
            url = str((unit.get("more") or {}).get("url") or src["base"])
            name = f"{fetched.slug(title)[:80]}-{int(unit['key'])}"
            meta = {"url": url, "revision": None, "license": src["license"], "license_url": src["license_url"],
                    "credit": src["credit"]}
            head = f"{title}\n\nFrom iFixit, {url}. {src['credit']}\n\n"
        elif src["reader"] == "commons":
            more = unit.get("more") or {}
            file_url = readers.https_on(more.get("file"), src.get("file_hosts"))
            if not file_url:
                return self._not_kept(run, src, unit, off_goal=False)
            if not fits_goal(f"{unit['title']}\n{more.get('description') or ''}", need):
                return self._not_kept(run, src, unit, off_goal=True)
            run["now"] = f"Getting the picture {shown_title(unit['title'], 'a picture')} ({k} of at most {src['max_items']})"
            self._now(build_id, run)
            status, body = self._fetch(build_id, src, file_url, accept="image/*", max_bytes=cap + 1)
            kind = readers.picture_kind(body) if status == 200 and len(body) <= cap else ""
            if not kind:                               # the file's own first bytes, never its address, say what it is
                return self._not_kept(run, src, unit, off_goal=False)
            title = _safe_title(unit["title"], "A picture")
            name = f"{fetched.slug(title)[:80]}-{int(unit['key'])}"
            keep_file = (f"{name}.{kind}", bytes(body))
            url = readers.https_on(more.get("url"), {src["host"]}) or src["base"]
            licence, who = str(more.get("license") or "")[:100], str(more.get("who") or "")[:120]
            meta = {"url": url, "revision": None, "license": licence, "license_url": src["license_url"],
                    "credit": f"{src['credit']} By {who}."}
            text = (f"Picture file: {name}.{kind}\nBy: {who}\nLicense: {licence}\n"
                    f"{more.get('description') or ''}\n")
            head = f"{title}\n\nFrom {src['name']}, {url}.\n\n"
        else:
            book = int(unit["key"])
            run["now"] = (f"Reading the book {shown_title(unit['title'], 'a book')} from Project Gutenberg "
                          f"({k} of at most {src['max_items']})")
            self._now(build_id, run)
            status, body = self._fetch(build_id, src, src["text"].format(id=book), accept="text/plain",
                                       max_bytes=cap + 1)
            if status != 200 or not body:
                return self._not_kept(run, src, unit, off_goal=False)
            title = _safe_title(unit["title"], f"Project Gutenberg ebook {book}")
            text = _gutenberg_body(body.decode("utf-8", errors="replace"))
            if not fits_goal(f"{unit['title']}\n{text[:fit]}", need):
                return self._not_kept(run, src, unit, off_goal=True)
            url = src["page"].format(id=book)
            name = fetched.slug(title, prefix=f"pg{book}")
            meta = {"url": url, "revision": None, "license": src["license"], "license_url": src["license_url"],
                    "credit": src["credit"], "cut": len(body) > cap}
            head = f"{title}\n\nFrom Project Gutenberg, {url}. {src['credit']}\n\n"
        raw = head + text
        cut = len(raw.encode("utf-8")) > cap
        if cut:
            raw = raw.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
            meta["cut"] = True
        screen = getattr(self.host.ports, "screen_knowledge", None)
        try:
            if not callable(screen):
                raise NotBuilt("Drawbridge's text screen", "L4")
            kept, why = screen(raw)
        except Exception:  # noqa: BLE001 - no screen, or one that breaks: nothing is kept (fail closed)
            kept, why = None, "Drawbridge's text screen could not run, so nothing of it was kept"
        if kept is None:
            run["kept_out"][sid] = int(run["kept_out"].get(sid) or 0) + 1
            run.setdefault("kept_why", {}).setdefault(sid, str(why or "it could not be checked")[:200])
            run["lines"] = (run["lines"] + [f"Drawbridge kept {shown_title(title, 'one item')} out: {why}."])[-40:]
            return
        meta.update(source=sid, title=title, topic=unit.get("topic"), fetched_at=settings_mod.now())
        path = fetched.save(self.host.state, sid, name, kept, meta)
        if keep_file is not None:
            fetched.save_file(self.host.state, sid, keep_file[0], keep_file[1])
        index = getattr(self.host.ports, "knowledge_index", None)
        said = ""
        if callable(index):
            try:
                said = index(path)
            except Exception as exc:  # noqa: BLE001 - kept; the next search's scan indexes it
                said = f"it is indexed at the next search ({type(exc).__name__})"
        run["counts"][sid] = k
        got = run.setdefault("got", {})
        got[f"{sid}|{unit.get('topic') or ''}"] = int(got.get(f"{sid}|{unit.get('topic') or ''}") or 0) + 1
        run["bytes"] = int(run.get("bytes") or 0) + path.stat().st_size
        run["items"] = (run["items"] + [{"source": sid, "title": title[:200], "file": path.name,
                                         "size": path.stat().st_size, "indexed": not said}])[-60:]
        if said:
            run["lines"] = (run["lines"] + [f"{shown_title(title, 'One item')} is kept but not searchable yet: "
                                            f"{said}."])[-40:]

    # -- while planning: what could be downloaded (D99) -----------------------------------------------------
    def find(self, build_id: str, topics: list[str]) -> dict:
        """The catalogs' answers for these screened topics, kept on the build for the plan card. Only places her
        search Yes names, under Toolsmith's search subject. Each place has its own timeout and the round its own
        time (``budget.catalogs_s_per_plan``); a place that fails is out of this plan and the rest go on."""
        from .ports import KNOWLEDGE_USER_AGENT
        cfg = _cfg()
        places = getattr(self.host.ports, "search_places", None)
        allowed = set(places()) if callable(places) else set()
        ends = time.monotonic() + float(cfg["budget"]["catalogs_s_per_plan"])
        finds: dict[str, list] = {}
        sent: dict[str, list[str]] = {}
        down: dict[str, str] = {}

        def ask(sid: str, row: dict) -> None:
            got: dict[str, dict] = {}
            finds[sid] = []
            todo = list(topics[:max(1, int(row.get("per_plan") or 1))])
            retries = int(row.get("word_retry") or 0)
            while todo:
                topic = todo.pop(0)
                if time.monotonic() >= ends:
                    break                          # a budget limits follow-ups: its first topic was asked
                url = row["search"].format(q=urllib.parse.quote(topic, safe=""), n=int(row.get("n") or 5))
                try:
                    status, _h, body = self.host.ports.http_send(
                        "GET", url, subject="toolsmith:search", headers={"Accept": "*/*", "User-Agent": KNOWLEDGE_USER_AGENT},
                        max_bytes=int(row.get("max_bytes") or 500_000), timeout=float(row.get("timeout_s") or 8),
                        phase="build")
                except (Refused, OSError, ValueError) as exc:
                    down[sid] = type(exc).__name__
                    break
                sent.setdefault(sid, []).append(topic)
                if int(status) != 200:
                    down[sid] = str(status)
                    break
                items = CATALOGS[row["reader"]](bytes(body), row)
                for item in items:
                    got.setdefault(str(item["id"]), item)
                finds[sid] = list(got.values())[:40]
                words = [w for w in topic.lower().split() if len(w) >= 3 and w not in _COMMON]
                if not items and retries > 0 and len(words) > 1:
                    # A catalog reads a modern phrase as all-or-nothing: once more with the topic's own longest word.
                    retries -= 1
                    todo.insert(0, max(words, key=len))

        rows = {sid: row for sid, row in (cfg.get("catalogs") or {}).items()
                if row.get("host") in allowed and row.get("reader") in CATALOGS}
        if rows:
            pool = ThreadPoolExecutor(max_workers=len(rows), thread_name_prefix="catalog")
            jobs = {pool.submit(ask, sid, row): sid for sid, row in rows.items()}
            _done, late = wait_all(jobs, timeout=float(cfg["budget"]["catalogs_s_per_plan"]) + 10)
            pool.shutdown(wait=False, cancel_futures=True)
            for f in late:
                down.setdefault(jobs[f], "time")
            finds = {sid: list(v) for sid, v in finds.items()}

        def fn(b: dict) -> None:
            if not b.get("planning"):
                return
            b["knowledge_finds"] = finds
            place = b.setdefault("searches_by_place", {})
            for sid, words in sent.items():
                place[sid] = list(dict.fromkeys(list(place.get(sid) or []) + words))[:40]
            if down:
                b["knowledge_down"] = {sid: _cfg()["catalogs"][sid]["short"] for sid in down}
        self.host.store.change(build_id, fn)
        return {"ok": True, "found": {sid: len(v) for sid, v in finds.items()}}

    # -- one big download, one step at a time (D99, ``knowledge_downloads``) -----------------------------------
    def _send_for(self, build_id: str, src: dict):
        def send(url: str, headers: dict, max_bytes: int, timeout: float) -> tuple[int, bytes]:
            return self._fetch(build_id, src, url, accept=headers.get("Accept", "*/*"), max_bytes=max_bytes,
                               headers={k: v for k, v in headers.items() if k != "Accept"}, timeout=timeout)
        return send

    def _download(self, build_id: str, b: dict, run: dict, kp: dict, unit: dict) -> None:
        offer = next((o for o in kp.get("offers") or [] if o.get("value") == unit.get("value")), None)
        row = (_cfg().get("catalogs") or {}).get(str(unit.get("source")))
        if offer is None or not isinstance(row, dict):
            return                                 # not on her plan: nothing is read
        state = run.setdefault("downloads", {}).setdefault(offer["value"], {"title": offer["title"], "line": ""})
        try:
            if row["reader"] == "kiwix":
                more = self._kiwix(build_id, b, run, kp, offer, row, unit, state)
            else:
                more = self._file(build_id, run, kp, offer, row, unit, state)
        except Exception as exc:  # noqa: BLE001 - one download's problem ends that download, never the fetch
            # Only our own failures have words for her; any other error's text (an address, a path) stays out.
            ours = isinstance(exc, (downloads.DownloadFailed, SourceDown, fetched.StoreAway))
            why = str(exc).strip().rstrip(".") if ours else f"the Core hit {type(exc).__name__}"
            if isinstance(exc, OSError) and not ours:      # the drive went away or filled up during this step
                why = fetched.away_line(self.host.state, 1).rstrip(".") or why
            state.update(failed=True, line=f"{offer['title']} was not downloaded: {why or type(exc).__name__}")
            run["lines"] = (run["lines"] + [state["line"]])[-40:]
            if isinstance(exc, downloads.DownloadFailed):
                # A bad file. A drive or a network that went away keeps what it has (a big download is not lost).
                downloads.remove_part(fetched.root(self.host.state) / downloads.package_id(offer["source"], offer["id"])
                                      / f".{fetched.slug(offer['id'])}.part")
            return
        if more:
            run["queue"].insert(0, unit)           # the same download's next step, before anything else

    def _folder(self, offer: dict) -> tuple[Path, Path]:
        pid = downloads.package_id(offer["source"], offer["id"])
        folder = fetched.package_folder(self.host.state, pid)
        return folder, folder / f".{fetched.slug(offer['id'])}.part"

    def _kiwix(self, build_id: str, b: dict, run: dict, kp: dict, offer: dict, row: dict, unit: dict, state: dict) -> bool:
        folder, part = self._folder(offer)
        pid = folder.name
        src = {"short": row["short"]}
        stage = unit.get("stage")
        if stage == "start":
            run["now"] = f"Checking the download {offer['title']}"
            status, body = self._fetch(build_id, src, offer["meta"], accept="*/*", max_bytes=2_000_000)
            meta = readers.meta4_of(body) if status == 200 else None
            if meta is None:
                raise downloads.DownloadFailed("its size and hash could not be read")
            if offer["bytes"] and meta["size"] > offer["bytes"] * 1.5:
                raise downloads.DownloadFailed("it has grown a lot since the plan was made")
            urls = [u for h in row["download_hosts"] for u in meta["urls"] if readers.https_on(u, {h})]
            if not urls:
                raise downloads.DownloadFailed("no mirror your plan names lists it")
            fetched.folder_for(self.host.state, pid, need=meta["size"])    # room first, on the drive she chose
            unit.update(stage="bytes", url=urls[0], spare=urls[1:], sha=meta["sha256"], size=meta["size"], file=meta["file"])
            return True
        if stage == "bytes":
            size = int(unit["size"])
            run["now"] = f"Downloading {offer['title']}"
            try:
                at = downloads.fetch_range(self._send_for(build_id, src), unit["url"], part, size)
            except (SourceDown, OSError):
                if not unit.get("spare"):
                    raise
                unit["url"] = unit["spare"].pop(0)   # the next mirror on her card; what is here so far stays
                return True
            state["line"] = f"downloading ({downloads.size_said(at)} of {downloads.size_said(size)})"
            run["now"] = f"Downloading {offer['title']} ({downloads.size_said(at)} of {downloads.size_said(size)})"
            if at < size:
                return True
            if at != size or downloads.sha256_of(part) != unit["sha"]:
                raise downloads.DownloadFailed("the file did not match its hash, so it was not kept")
            target = folder / str(unit["file"])
            os.replace(part, target)
            downloads.write_manifest(folder, pid, offer, unit["url"], unit["sha"], size)
            self._register(pid, offer, unit["url"])
            unit.update(stage="scan", at=0, picked=[])
            return True
        target = folder / str(unit["file"])
        need = {w for t in kp.get("topics") or [] for w in subject_words(t)} | subject_words(b.get("goal"))
        most = int(row.get("extract_items") or 400)
        if stage == "scan":
            run["now"] = f"Looking through {offer['title']} for your topics"
            every = 0 < int(offer.get("articles") or 0) <= most
            at, picked, ended = downloads.picked_entries(target, int(unit["at"]), list(unit["picked"]), need, every, most,
                                                         lambda text, words: bool(subject_words(text) & words))
            unit.update(at=at, picked=picked)
            if ended:
                unit["stage"] = "read"
            return True
        batch, rest = unit["picked"][:downloads.READ_PER_STEP], unit["picked"][downloads.READ_PER_STEP:]
        run["now"] = f"Keeping {offer['title']}'s articles as text ({len(rest)} to go)"
        room = int(row.get("extract_kb") or 80000) * 1000 - int(state.get("kept_bytes") or 0)
        for title, text in downloads.read_articles(target, batch):
            if room <= 0:
                rest = []
                break
            room -= self._keep_text(run, pid, offer, row, title, text, unit["url"], state)
        unit["picked"] = rest
        if rest:
            return True
        n = int(state.get("kept") or 0)
        state.update(done=True, line=f"downloaded; {_plural(n, 'article', 'articles')} about your topics can be searched, "
                                     "and the whole file is kept in your knowledge folder")
        return False

    def _file(self, build_id: str, run: dict, kp: dict, offer: dict, row: dict, unit: dict, state: dict) -> bool:
        folder, _part = self._folder(offer)
        pid = folder.name
        url = readers.https_on(offer.get("file"), set(offer.get("hosts") or []))
        if not url:
            raise downloads.DownloadFailed("its address is not the one on your plan")
        run["now"] = f"Downloading {offer['title']}"
        most = int(row.get("download_max_mb") or 50) * 1_000_000
        fetched.folder_for(self.host.state, pid, need=most)
        status, body = self._fetch(build_id, {"short": row["short"]}, url, accept="*/*", max_bytes=most + 1,
                                   timeout=downloads.STEP_TIMEOUT_S)
        if status != 200 or not body:
            raise downloads.DownloadFailed(f"it answered {status}")
        sha = hashlib.sha256(body).hexdigest()
        target = None
        try:
            if offer.get("format") == "pdf":
                if not bytes(body).startswith(b"%PDF-"):
                    raise downloads.DownloadFailed("it is not the PDF its record says")
                target = folder / f"{fetched.slug(offer['id'])}.pdf"
                target.write_bytes(body)
                text = downloads.pdf_text(target)
            else:
                text = bytes(body).decode("utf-8", errors="replace")
                if "\x00" in text[:4000]:
                    raise downloads.DownloadFailed("it is not text PuffPAI can read")
            if not text.strip():
                raise downloads.DownloadFailed("it has no text PuffPAI can read")
            self._keep_text(run, pid, offer, row, offer["title"], text[:downloads.TEXT_MAX], url, state)
        finally:
            if target is not None and not state.get("kept"):
                target.unlink(missing_ok=True)     # a held or unread text leaves nothing behind
        if state.get("kept"):                      # only a text Drawbridge passed gets a manifest and a Registry row
            downloads.write_manifest(folder, pid, offer, url, sha, len(body))
            self._register(pid, offer, url)
        cut = len(body) > most or len(text) > downloads.TEXT_MAX
        state.update(done=bool(state.get("kept")), failed=not state.get("kept"),
                     line=("downloaded and kept as text" + (", cut at its size cap" if cut else "")) if state.get("kept")
                     else f"{offer['title']} was not kept: Drawbridge held its text")
        return False

    def _keep_text(self, run: dict, pid: str, offer: dict, row: dict, title: str, text: str, url: str, state: dict) -> int:
        """One text through Drawbridge's rules, kept in the package's folder for the Library. Bytes kept."""
        safe = _safe_title(title, offer["title"])
        credit = f"{row['credit']} License: {offer['license']}."
        raw = f"{safe}\n\nFrom {offer['title']} ({row['short']}), {url}. {credit}\n\n{text}"
        screen = getattr(self.host.ports, "screen_knowledge", None)
        try:
            if not callable(screen):
                raise NotBuilt("Drawbridge's text screen", "L4")
            kept, _why = screen(raw)
        except Exception:  # noqa: BLE001 - no screen, or one that breaks: nothing is kept (fail closed)
            kept = None
        if kept is None:
            return 0
        n = int(state.get("kept") or 0) + 1
        name = f"{fetched.slug(safe)[:80]}-{n}"
        meta = {"url": url, "revision": None, "license": offer["license"], "license_url": row.get("license_url"),
                "credit": credit, "source": pid, "title": safe, "topic": None, "fetched_at": settings_mod.now()}
        path = fetched.save(self.host.state, pid, name, kept, meta)
        size = path.stat().st_size
        index = getattr(self.host.ports, "knowledge_index", None)
        if callable(index):
            try:
                index(path)
            except Exception:  # noqa: BLE001 - kept; the next search's scan indexes it
                pass
        state.update(kept=n, kept_bytes=int(state.get("kept_bytes") or 0) + size)
        run["bytes"] = int(run.get("bytes") or 0) + size
        run["items"] = (run["items"] + [{"source": pid, "title": safe[:200], "file": path.name, "size": size,
                                         "indexed": True}])[-60:]
        return size

    def _register(self, pid: str, offer: dict, url: str) -> None:
        """Its Registry row, so the brain sees it among what is installed. A row that can't be added loses nothing:
        the text is in the Library either way."""
        registry = getattr(self.host, "registry", None)
        if registry is None:
            return
        try:
            if registry.get(pid) is None:
                registry.add({"id": pid, "kind": "knowledge", "name": offer["title"][:80], "origin": f"adopted:{url}",
                              "status": "stopped"})
        except Exception:  # noqa: BLE001
            logger.exception("knowledge package %s not listed", pid)

    def _now(self, build_id: str, run: dict) -> None:
        def fn(b: dict) -> None:
            if isinstance(b.get("knowledge_run"), dict):
                b["knowledge_run"]["now"] = run["now"]
        try:
            self.host.store.change(build_id, fn)
        except Exception:  # noqa: BLE001 - a progress line never stops the fetch
            pass


def _wiki_titles(body: bytes) -> list[tuple[str, str]]:
    try:
        got = json.loads(body.decode("utf-8", errors="replace"))
        rows = (got.get("query") or {}).get("search") or []
    except (ValueError, AttributeError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        t = r.get("title") if isinstance(r, dict) else None
        if isinstance(t, str) and 0 < len(t) <= 250:
            out.append((t, t))
    return out


def _wiki_page(body: bytes, base: str = "https://en.wikipedia.org/wiki/") -> tuple[str, str, str, int, int, str] | None:
    """``(title, text, url, revision, page id, short description)`` of one article, or None (missing, a page of
    meanings, no text). The short description ("2012 studio album by ...") is read only by the goal check."""
    try:
        got = json.loads(body.decode("utf-8", errors="replace"))
        pages = (got.get("query") or {}).get("pages") or []
        page = pages[0] if isinstance(pages, list) and pages else None
    except (ValueError, AttributeError, KeyError):
        return None
    if not isinstance(page, dict) or page.get("missing") or page.get("invalid"):
        return None
    props = page.get("pageprops") if isinstance(page.get("pageprops"), dict) else {}
    if "disambiguation" in props:
        return None
    about = props.get("wikibase-shortdesc")
    about = " ".join(about.split())[:200] if isinstance(about, str) else ""
    title, text = page.get("title"), page.get("extract")
    if not isinstance(title, str) or not isinstance(text, str) or len(text.strip()) < 200:
        return None
    pageid = page.get("pageid")
    revs = page.get("revisions") or []
    revision = revs[0].get("revid") if revs and isinstance(revs[0], dict) else None
    url = page.get("canonicalurl") or page.get("fullurl") or ""
    if not (isinstance(url, str) and url.startswith(base) and url.isascii()):
        url = base + urllib.parse.quote(title.replace(" ", "_"))
    return (title[:200], text, url, revision if isinstance(revision, int) else 0,
            pageid if isinstance(pageid, int) and pageid > 0 else 0, about)


_ENTRY = re.compile(r"<entry\b.*?</entry>", re.S | re.I)
_BOOK = re.compile(r"/ebooks/(\d{1,7})(?:\.opds)?[\"'</]", re.I)
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title>", re.S | re.I)


def _opds_books(body: bytes) -> list[tuple[int, str]]:
    """``(ebook number, title)`` per book in a Gutenberg OPDS search answer (read with patterns, no XML
    parser, so no entity tricks)."""
    text = body.decode("utf-8", errors="replace")
    out = []
    for m in _ENTRY.finditer(text):
        entry = m.group(0)
        num = _BOOK.search(entry)
        title = _TITLE.search(entry)
        if not num or not title:
            continue
        t = html.unescape(re.sub(r"<[^>]+>", " ", title.group(1)))
        t = " ".join(t.split())[:200]
        if t:
            out.append((int(num.group(1)), t))
    return out


#: One reader per kind of fetched source (a row's ``reader``): how it is asked and what its answer finds.
READERS: dict[str, dict] = {
    "wiki": {"accept": "application/json", "found": lambda body, src: _wiki_titles(body)},
    "gutenberg": {"accept": "application/atom+xml", "found": lambda body, src: _opds_books(body)},
    "ifixit": {"accept": "application/json", "found": lambda body, src: readers.ifixit_found(body)},
    "commons": {"accept": "application/json", "found": lambda body, src: readers.commons_found(body, src["file_hosts"])},
}
#: What each catalog's answer offers (a row's ``reader``).
CATALOGS = {"kiwix": lambda body, row: readers.kiwix_found(body, row["meta_host"]), "datagov": readers.datagov_found,
            "huggingface": readers.hf_found}

_START = re.compile(r"^\s*\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.I | re.M)
_END = re.compile(r"^\s*\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.I | re.M)


# -- a job proved by the library (lane I19e, D71) --------------------------------------------------------------

#: Words that say what kind of want a job is (a guide, a book, step by step), never what it is about.
KIND_WORDS = frozenset("""
guide guides guidance manual manuals howto howtos tos tutorial tutorials step steps instruction instructions book
books article articles tip tips diagram diagrams picture pictures info information reference references
detailed specific practice practices first question questions answer answers explain explains provide provides
give gives show shows help helps tool tools add bot
""".split())
#: The job states a library proof may end: no app works for the job.
PROVABLE = ("nothing_found", "not_yet")
PROVE_WORDS = 6                                 # at most this many of the job's words are searched
PROVE_QUERIES = 60                              # at most this many searches per job
PROVE_PAIRS = 12                                # at most this many side by side word pairs are sure of a search
#: A job is working from the library only with at least this many passages, from at least this many fetched items
#: (one passage, or one page alone, is too thin: a glossary page would pass).
MIN_PASSAGES = 3
MIN_ITEMS = 2
PROVE_HITS = 200                                # hits read per search (other builds' files are left out after)


def _singular(word: str) -> str:
    """The index has no stemming, so a plural is searched as its singular ("recipes" finds "recipe")."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "is", "us")):
        return word[:-1]
    return word


def _prove_tokens(text: Any) -> list[str]:
    """One text's searchable words, in order: 3 letters or more, not a small word, not a kind-of-want word."""
    return [w for w in re.findall(r"[a-z]+", _fold(re.sub(r"https?://\S+", " ", str(text or ""))))   # no web address
            if len(w) >= 3 and w not in _COMMON and w not in KIND_WORDS and _singular(w) not in KIND_WORDS]


def prove_words(*texts: Any) -> list[str]:
    """The words a job is searched with, in order: each of :func:`_prove_tokens` once (a plural and its singular
    count once), at most :data:`PROVE_WORDS`."""
    out: list[str] = []
    for text in texts:
        for w in _prove_tokens(text):
            if _singular(w) not in {_singular(x) for x in out}:
                out.append(w)
    return out[:PROVE_WORDS]


def prove_pairs(*texts: Any) -> list[list[str]]:
    """The word pairs that stand side by side in each text, most telling text first, each once, at most
    :data:`PROVE_PAIRS`. These are the job's real phrases, so each one is sure of a search."""
    out: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for text in texts:
        tokens = _prove_tokens(text)
        for a, b in zip(tokens, tokens[1:]):
            key = frozenset((_singular(a), _singular(b)))
            if len(key) == 2 and key not in seen:
                seen.add(key)
                out.append([a, b])
    return out[:PROVE_PAIRS]


def prove_queries(words: list[str], pairs: list[list[str]] | None = None) -> list[str]:
    """The searches, most words first: every word, then fewer, down to two (one when there is only one), at
    most :data:`PROVE_QUERIES`. The index finds a passage only when it has every word of a search, and it has
    no stemming, so a search with a plural is also asked with its singulars ("recipes" finds "recipe"). The cap
    is spent fairly, so no size is starved: every word together first, then *pairs* and the side by side pairs
    of *words*, then each size takes a turn."""
    from itertools import combinations
    chosen: dict[str, int] = {}                    # a search -> how many words it has

    def ask(combo: Any) -> None:
        for q in dict.fromkeys((" ".join(combo), " ".join(_singular(w) for w in combo))):
            if len(chosen) < PROVE_QUERIES:
                chosen.setdefault(q, len(combo))

    sizes = [list(combinations(words, n)) for n in range(len(words), min(2, len(words)) - 1, -1)] if words else []
    if sizes:
        ask(sizes[0][0])
    for pair in list(pairs or []) + [list(p) for p in zip(words, words[1:])]:
        ask(pair)
    for turn in range(max((len(s) for s in sizes), default=0)):
        for combos in sizes:
            if turn < len(combos):
                ask(combos[turn])
    return sorted(chosen, key=lambda q: -chosen[q])


def provable(job: dict) -> bool:
    """A job a library proof may end: no app works for it, she pasted no app for it and ticked none."""
    plan = job.get("plan") or {}
    return ((job.get("run") or {}).get("state") in PROVABLE and not job.get("pasted")
            and plan.get("reuse_by") not in ("tick", "store"))


def library_proof(b: dict, job: dict, search: Any, root: Any) -> dict | None:
    """Search ONLY this build's fetched items (``knowledge_run.items``, under *root*, the knowledge folder) with
    the job's own words (its sample ask, what it does, its name; the goal's words only when the job has none).
    *search* is the brain's own ``search_knowledge`` search (``Library.search``). ``{count, items, sources,
    titles, query}``: the first search that is :func:`enough`, else the one with the most passages; None when no
    passage came back."""
    from pathlib import Path
    run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
    if run.get("state") != "done":
        return None
    mine = {(str(i.get("source")), str(i.get("file"))): i for i in run.get("items") or []
            if isinstance(i, dict) and i.get("file")}
    if not mine:
        return None
    root = Path(root).resolve()
    name = " ".join(str(job.get("name") or "").split())
    ask = " ".join(str(job.get("sample_ask") or "").split())
    if ask == f"What does {name} show right now?":
        ask = ""                                   # the made-up ask fits apps, not a want for knowledge
    words = prove_words(ask, job.get("does"), name) or prove_words(b.get("goal"))
    best: dict | None = None
    for query in prove_queries(words, prove_pairs(name, ask, job.get("does"))):
        hits = []
        for h in search(query, PROVE_HITS) or []:
            p = Path(str(h.get("path") or ""))
            if p.parent.parent.resolve() == root and (p.parent.name, p.name) in mine:
                hits.append(mine[(p.parent.name, p.name)])
        if not hits:
            continue
        items = list({id(i): i for i in hits}.values())
        sources: dict[str, int] = {}
        for i in items:
            sources[str(i["source"])] = sources.get(str(i["source"]), 0) + 1
        titles = [t for t in (_safe_title(i.get("title"), "") for i in items) if t]
        got = {"count": len(hits), "items": len(items), "sources": sources, "titles": titles[:10], "query": query}
        if enough(got):
            return got                              # the search with the most of the job's words that is enough
        if best is None or (got["count"], got["items"]) > (best["count"], best["items"]):
            best = got
    return best


def enough(got: dict | None) -> bool:
    """Enough to call a job working: at least :data:`MIN_PASSAGES` passages from :data:`MIN_ITEMS` items."""
    return bool(got) and int(got["count"]) >= MIN_PASSAGES and int(got["items"]) >= MIN_ITEMS


def thin_line(got: dict) -> str:
    """Her line when the library has a little on a job, but not enough to call it done."""
    n = int(got["count"])
    titles = ", ".join(f"\"{t}\"" for t in got.get("titles", [])[:3])
    return (f"Your library has {_plural(n, 'passage', 'passages')} on this so far" + (f": {titles}" if titles else "")
            + ". Not enough to call this job done.")


def proof_line(job: dict, got: dict) -> str:
    """Her line for a job the library proves: how many passages, from which sources, a few titles. It never says
    the library does the job (D123): a match can be weak."""
    kinds = {"wikipedia": ("Wikipedia article", "Wikipedia articles"),
             "gutenberg": ("public domain book", "public domain books")}
    for sid, row in _cfg()["fetch"].items():
        kinds.setdefault(sid, (f"{row['short']} {_one_kind(sid)}", f"{row['short']} {row['kind']}"))
    parts = [_plural(n, *kinds.get(sid, ("item", "items"))) for sid, n in got["sources"].items()]
    n = int(got["count"])
    line = (f"No app does this yet, but your library has some reading on this: {_plural(n, 'passage', 'passages')} from "
            f"{' and '.join(parts)}")
    if got.get("titles"):
        line += ", such as " + ", ".join(f"\"{t}\"" for t in got["titles"][:3])
    name = " ".join(str(job.get("name") or "").split())
    ask = " ".join(str(job.get("sample_ask") or "").split())
    if ask and ask != f"What does {name} show right now?":
        return line + f". Ask the chat, for example: \"{ask[:200]}\"."
    return line + ". Ask the chat about it."


def _gutenberg_body(text: str) -> str:
    """The book itself, without Project Gutenberg's long header and license footer (its address and license
    stay in ``meta.json`` and at the top of the file)."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    start, end = _START.search(text), _END.search(text)
    if start and end and end.start() > start.end():
        return text[start.end():end.start()].strip() + "\n"
    if start:
        return text[start.end():].strip() + "\n"
    return text.strip() + "\n"


__all__ = ["BEST_DOWNLOADS", "READERS", "UNTICKED_OVER", "best_offers", "look_rows", "sent_line", "ticked_of",
           "Fetcher", "end_unfinished", "finish", "start_run", "MAX_TOPICS", "NO_TOPICS_LINE", "SourceDown", "card_lines", "clean_topics", "per_topic",
           "MIN_ITEMS", "MIN_PASSAGES", "enough", "library_proof", "proof_line", "thin_line", "provable", "prove_pairs", "prove_queries", "prove_words",
           "plan_of", "progress", "rows", "summary"]
