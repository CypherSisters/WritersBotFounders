"""What the writer said about the bible: her corrections, and her choices on the editor's desk (CONTRACT.md).

``apply_corrections(bible, records)`` puts each correction in ``corrections.json`` on top of a bible, in the order
they were made, and says for each whether it could be applied. It is deterministic and safe to run again on the same
bible, so the reading runs it before every write and a re-read never undoes a correction. What it can apply, matched
by the names in her words: a relationship's kind ("Tom is Jennifer's cousin, not her brother"), an alias ("Jen is
also called Jenny"), a character's role or importance ("Ilsa is the antagonist",
"Tom is a minor character"), a place's kind ("Storm Harbor is a village").
Every bible entry it touches gets ``"corrected": true``. Anything else stays a note, shown on its tab.

``apply_desk(bible, items)`` puts the writer's Keep, Dismiss and notes from ``desk.json`` back on the desk items of
the same id.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any

GROUPS = [("brother", "sister", "sibling"), ("half-brother", "half-sister", "half-sibling"),
          ("mother", "father", "parent"), ("son", "daughter", "child"),
          ("stepmother", "stepfather", "stepparent"), ("stepson", "stepdaughter", "stepchild"),
          ("grandmother", "grandfather", "grandparent"), ("grandson", "granddaughter", "grandchild"),
          ("husband", "wife", "spouse"), ("aunt", "uncle"), ("niece", "nephew"), ("cousin",), ("partner",),
          ("fiance", "fiancee", "fiancé", "fiancée"), ("ex-husband", "ex-wife", "ex"), ("best friend", "friend"),
          ("enemy",), ("rival",), ("lover",), ("boss", "employer"), ("employee", "assistant"), ("mentor",),
          ("student", "pupil", "apprentice"), ("neighbour", "neighbor"), ("colleague", "coworker")]
GROUP_OF = {w: g for g in GROUPS for w in g}
INVERSE = {"sibling": "sibling", "half-sibling": "half-sibling", "parent": "child", "child": "parent",
           "stepparent": "stepchild", "stepchild": "stepparent", "grandparent": "grandchild",
           "grandchild": "grandparent", "spouse": "spouse", "aunt": "niece or nephew", "niece": "aunt or uncle",
           "cousin": "cousin", "partner": "partner", "fiance": "fiance", "ex": "ex", "friend": "friend",
           "enemy": "enemy", "rival": "rival", "lover": "lover", "boss": "employee", "employee": "boss",
           "mentor": "student", "student": "mentor", "neighbour": "neighbour", "colleague": "colleague"}
FAMILY = {"parent": "parents", "child": "children", "spouse": "partners", "partner": "partners"}
IMPORTANCE = ["main", "supporting", "minor"]
ROLES = ["love interest", "protagonist", "antagonist",
         "deuteragonist", "narrator", "villain", "heroine", "hero", "sidekick", "foil"]
PLACE_KINDS = ["town", "city", "village", "island", "house", "farm", "ship", "harbor", "harbour", "port", "country",
               "kingdom", "forest", "river", "lake", "mountain", "castle", "palace", "school", "inn", "tavern",
               "street", "planet", "moon", "station", "valley", "desert", "church", "hospital", "shop", "cave",
               "cottage", "mansion", "city state", "region", "world", "continent", "sea", "bay", "beach", "camp"]
ALIAS = re.compile(r"(?:also called|also known as|known as|nicknamed|goes by|called|named)\s+[\"'“]?"
                   r"([A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*)*)")
NEG = re.compile(r"(?:\bnot|n't|\bnever)\s+(?:(?:a|an|the|her|his|their|my|really|actually)\s+)*$")
TIME_WORDS = re.compile(r"\b(when|before|after|year|years|day|days|month|week|age|old|timeline|night|morning)\b")
SCIENCE_WORDS = re.compile(r"\b(science|tide|tides|moon|orbit|gravity|physics|planet|speed|light|distance)\b")


def _k(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", text.lower()).split())


def _words_in(text: str, vocab: list[str]) -> tuple[list[str], list[str]]:
    """(said, negated): the vocabulary words in the text, in order; a word after "not" or "never" is negated."""
    low = text.lower()
    pat = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in sorted(vocab, key=len, reverse=True)) +
                     r")(?![\w-])")
    said, neg = [], []
    for m in pat.finditer(low):
        (neg if NEG.search(low[max(0, m.start() - 40):m.start()]) else said).append(m.group(1))
    return said, neg


def _named(text: str, things: list[dict], extra: bool = True) -> list[dict]:
    """The characters or places the text names, in the order it names them: by name, alias, id, and (``extra``)
    a first name only one of them has."""
    low = " " + _k(re.sub(r"['’]s\b", "", text)) + " "
    firsts: dict[str, int] = {}
    for c in things:
        parts = str(c.get("name") or "").split()
        if parts:
            firsts[parts[0].lower()] = firsts.get(parts[0].lower(), 0) + 1
    found = []
    for c in things:
        names = [str(c.get("name") or ""), str(c.get("id") or "").replace("-", " ")]
        names += [a for a in c.get("aliases") or [] if isinstance(a, str)]
        parts = str(c.get("name") or "").split()
        if extra and parts and firsts.get(parts[0].lower()) == 1:
            names.append(parts[0])
        at = [low.find(" " + _k(n) + " ") for n in names if len(_k(n)) > 1]
        at = [i for i in at if i >= 0]
        if at:
            found.append((min(at), c))
    return [c for _i, c in sorted(found, key=lambda x: x[0])]


def _base(kind: str) -> str:
    """A relationship word's group head: "sister" -> "sibling", "wife" -> "spouse"."""
    g = GROUP_OF.get(kind.lower())
    if not g:
        return kind.lower()
    return g[-1] if g[-1] in INVERSE else g[0]


def _same(a: str, b: str) -> bool:
    return _base(a) == _base(b)


def _relation(bible: dict, text: str, chars: list[dict], correction: str) -> tuple[bool, str, list]:
    said, neg = _words_in(text, list(GROUP_OF))
    fix_said, _n = _words_in(correction, list(GROUP_OF)) if correction else ([], [])
    new = (fix_said or said or [None])[0]
    if not new:
        return False, "", []
    old = neg[0] if neg else None
    a = chars[0]
    others = [c for c in chars[1:] if c is not a]
    by_id = {c.get("id"): c for c in bible.get("characters") or []}
    if others:
        b = others[0]
    else:                                         # "Tom is her cousin, not her brother": the one she meant
        cands = set()
        for r in a.get("relationships") or []:
            if old is None or _same(r.get("kind", ""), old) or _same(r.get("kind", ""), INVERSE.get(_base(old), "")):
                cands.add(r.get("to"))
        for c in bible.get("characters") or []:
            for r in c.get("relationships") or []:
                if r.get("to") == a.get("id") and (old is None or _same(r.get("kind", ""), old)
                                                   or _same(r.get("kind", ""), INVERSE.get(_base(old), ""))):
                    cands.add(c.get("id"))
        cands.discard(a.get("id"))
        if len(cands) != 1 or next(iter(cands)) not in by_id:
            return False, "", []
        b = by_id[next(iter(cands))]
    inv = INVERSE.get(_base(new))
    touched = []
    for r in a.get("relationships") or []:
        if r.get("to") == b.get("id"):
            r["kind"], r["corrected"] = new, True
            touched.append(r)
    for r in b.get("relationships") or []:
        if r.get("to") == a.get("id"):
            if inv and not _same(r.get("kind", ""), inv):
                r["kind"] = inv
            r["corrected"] = True
            touched.append(r)
    if not touched:
        rel = {"to": b.get("id"), "kind": new, "note": "You corrected this.", "corrected": True}
        a.setdefault("relationships", []).append(rel)
    # family links follow the kind: a parent, a child or a partner; a sibling or a cousin is none of those
    for x, y in ((a, b), (b, a)):
        fam = x.setdefault("family", {"parents": [], "children": [], "partners": []})
        for key in ("parents", "children", "partners"):
            fam.setdefault(key, [])
            if y.get("id") in fam[key]:
                fam[key].remove(y.get("id"))
    rel_a = _base(new)                            # a is <new> of b
    if FAMILY.get(rel_a) == "parents":            # a is b's parent
        b["family"]["parents"].append(a.get("id"))
        a["family"]["children"].append(b.get("id"))
    elif FAMILY.get(rel_a) == "children":
        a["family"]["parents"].append(b.get("id"))
        b["family"]["children"].append(a.get("id"))
    elif FAMILY.get(rel_a) == "partners":
        a["family"]["partners"].append(b.get("id"))
        b["family"]["partners"].append(a.get("id"))
    a["corrected"] = b["corrected"] = True
    return True, f"{a.get('name')} is {b.get('name')}'s {new} now", [a.get("id"), b.get("id")]


def apply_one(bible: dict, what: str, correction: str = "") -> dict:
    """One correction on the bible. ``{"applied", "tab", "line"}``; the bible is changed in place."""
    text = " ".join(f"{what}. {correction}".split()) if correction and correction.lower() not in what.lower() \
        else " ".join(what.split())
    chars = [c for c in bible.get("characters") or [] if isinstance(c, dict)]
    places = [p for p in bible.get("places") or [] if isinstance(p, dict)]
    named = _named(text, chars)
    spots = _named(text, places, extra=False)
    tab = "characters" if named else "world" if spots else "timeline" if TIME_WORDS.search(text.lower()) \
        else "science" if SCIENCE_WORDS.search(text.lower()) else "characters"
    if named:
        ok, line, _ids = _relation(bible, text, named, correction)
        if ok:
            return {"applied": True, "tab": "characters", "line": line}
        said, _neg = _words_in(text, ROLES)
        weight, _neg = _words_in(text, IMPORTANCE)
        if (said or weight) and len(named) == 1:
            c, parts = named[0], []
            if weight:
                c["importance"] = weight[0]
                if weight[0] == "minor" and not c.get("write_up"):
                    from analysis import minor_write_up       # one line from the facts, as a reading makes it
                    c["write_up"] = minor_write_up(c)
                parts.append(f"a {weight[0]} character")
            if said:
                c["role"] = said[0]
                parts.append(f"the {said[0]}")
            c["corrected"] = True
            return {"applied": True, "tab": "characters", "line": f"{c.get('name')} is {' and '.join(parts)} now"}
        m = ALIAS.search(text)
        if m:
            c, alias = named[0], m.group(1).strip()
            if _k(alias) != _k(str(c.get("name") or "")):
                if _k(alias) not in {_k(a) for a in c.get("aliases") or []}:
                    c.setdefault("aliases", []).append(alias)
                c["corrected"] = True
                return {"applied": True, "tab": "characters", "line": f"{c.get('name')} is also called {alias} now"}
    if spots:
        said, _neg = _words_in(text, PLACE_KINDS)
        if said:
            spots[0]["kind"], spots[0]["corrected"] = said[0], True
            return {"applied": True, "tab": "world", "line": f"{spots[0].get('name')} is a {said[0]} now"}
    return {"applied": False, "tab": tab, "line": ""}


def apply_corrections(bible: dict, records: Any) -> list[dict]:
    """Every correction, in the order made: ``[{id, what, correction, made_at, applied, tab, line}]``."""
    out = []
    for r in records if isinstance(records, list) else []:
        if not isinstance(r, dict) or not str(r.get("what") or "").strip():
            continue
        try:
            got = apply_one(bible, str(r.get("what") or ""), str(r.get("correction") or ""))
        except (TypeError, ValueError, AttributeError, KeyError):
            got = {"applied": False, "tab": "characters", "line": ""}   # a bible it can't read: kept as a note
        out.append({k: r.get(k) for k in ("id", "what", "correction", "made_at")} | got)
    return out


def apply_desk(bible: dict, items: Any) -> None:
    """The writer's state and note on each desk item of the same id."""
    mine = {str(i.get("id")): i for i in items if isinstance(i, dict) and i.get("id")} if isinstance(items, list) else {}
    for d in bible.get("desk") or []:
        if isinstance(d, dict) and d.get("id") in mine:
            got = mine[d["id"]]
            if got.get("state") in ("open", "kept", "dismissed"):
                d["state"] = got["state"]
            d["writer_note"] = str(got.get("writer_note") or "")
