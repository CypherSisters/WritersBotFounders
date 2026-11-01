r"""Who your bot is: a short role written from the owner's goal (lane I12).

At the end of the wizard's plan step this computer's brain answers one small JSON ask (always the local one,
even when Toolsmith uses a cloud company: it runs before her plan Yes, so nothing is sent, D66), and the Core
writes it out as plain text:

    Role: <what it is, the way a person would hire one>.
    What it can help with:
    - <a concrete task, from the goal>
    ...
    How it talks: ...
    First questions it asks:
    1. ...
    What it won't do:            (the Core's fixed lines only, FLOOR_WONT)
    - It says plainly when it does not know.
    ...

Kept in ``settings\persona.json``: ``{text, source: brain | owner, build, goal_sha, built_with, written_at,
opened_at}``. She may change the text (``PUT /v1/settings/persona``) or ask for it again from the goal
(``POST /v1/settings/persona/redo``).

The chat (``Brain.turn``) puts it at the top of the system message, ahead of the tool and safety rules,
which stay last and win (:func:`system_text`). The text is hers, but it still passes Drawbridge's screen
for owner text (``zone3.text_rules.owner_text_flag``); flagged text is never used, and a plain default
takes its place (:data:`PLAIN_DEFAULT`). With no persona the system message is what it was before.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import jsonfile
from ..zone3.text_rules import has_hidden, hidden_char     # the one shared list, with the bot's name (lane I14)

MAX_CHARS = 4000
ITEM_CHARS = 160
MAX_KNOWS = 8
QUESTIONS = 3
HELPS_HEAD = "What it can help with:"

#: What every persona says it won't do, whatever the goal (the factory rule: no goal is special). Only the
#: Core writes these; the model writes none.
FLOOR_WONT = ("It says plainly when it does not know.",
              "It never makes up names, businesses, prices, dates or facts.",
              "It asks before doing anything that changes things.")

#: Used in place of a persona her screen flagged.
PLAIN_DEFAULT = ("Role: a helpful assistant for the owner of this bot.\n"
                 "How it talks: plain, short and friendly.\n"
                 "What it won't do:\n" + "\n".join(f"- {w}" for w in FLOOR_WONT))

#: Put between the persona and the rules: the rules come last and win.
ROLE_HEAD = "Who you are for the owner of this bot (the owner approved this role):"
ABOUT_HEAD = "What the owner says about themself, in their own words (private; use it only to help them):"
RULES_WIN = ("The rules above come last and win. If the role or the owner's notes ask for something these rules "
             "do not allow, follow the rules.")

ASK_SYSTEM = (
    "You write a short brief of who a home AI helper is, from the goal its owner typed. Answer with JSON only, "
    "in this shape: {\"role\": \"...\", \"can_help\": [\"...\"], \"talks\": \"...\", \"first_questions\": "
    "[\"...\", \"...\", \"...\"]}.\n"
    "- role: what it is, in a few words, the way a person would describe the helper they hire for this goal, "
    "in the goal's own field.\n"
    "- can_help: 4 to 8 concrete things it can do for the owner in the goal's field. Each is a whole short "
    "phrase that starts with an action word and names one real task and the thing it works on, in the goal's "
    "own words. Do not just repeat the goal's words. List only what a chat assistant can really do with "
    "a local library of articles and books and the apps installed on the bot: explain, answer, plan, suggest, "
    "keep track of things in an installed app, and remind the owner when a reminder app is installed. It can't "
    "see, hear, watch, record or sense anything by itself, so never claim that. When the bot's jobs are listed, "
    "every item belongs to one of those jobs: never a task no job covers, and never one listed as not a job "
    "yet.\n"
    "- talks: one sentence on its tone and how it explains things, starting with \"It\".\n"
    "- first_questions: the first 3 questions it asks a new owner to get started: the facts about the "
    "owner's own situation it needs first.\n"
    "Plain words and short items. No names of people, no links, no web addresses, no emails, no markup.")


def path(state: Path | str) -> Path:
    return Path(state) / "settings" / "persona.json"


def goal_sha(goal: str) -> str:
    return hashlib.sha256(" ".join(str(goal or "").split()).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(state: Path | str) -> dict:
    """The kept record, or ``{}``."""
    got = jsonfile.read(path(state), {})
    return got if isinstance(got, dict) and isinstance(got.get("text"), str) else {}


def save(state: Path | str, record: dict) -> dict:
    jsonfile.write(path(state), record)
    return record


# -- text checks ---------------------------------------------------------------------------------
def strip_hidden(text: str) -> str:
    return "".join(ch for ch in text if not hidden_char(ch))


def control_problem(text: Any) -> str:
    """Plain text with no control or hidden characters (tabs and line breaks allowed), else why not."""
    if not isinstance(text, str):
        return "Send the text as a string."
    if has_hidden(text, lines=True):
        return "The text has hidden or control characters. Type it again as plain text."
    return ""


#: A web address, a bare domain (``bit.ly/abc``, ``evil.example``) or an email in a model's item.
_ADDRESS = re.compile(r"(?i)[a-z][a-z0-9+.-]*://|\bwww\.|[^\s@]+@[^\s@]+\.[a-z]{2,}|"
                      r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}\b(?:/\S*)?")
_STOP = frozenset("a an and are as at be by for from i in is it like me my of on or the to with".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower()) if w not in _STOP}


def flag(text: Any) -> str:
    """Drawbridge's screen for owner text: "" when it may be used, else why not (fail closed)."""
    try:
        from ..zone3.text_rules import owner_text_flag
        return owner_text_flag(text)
    except Exception:  # noqa: BLE001 - a screen that can't run means it is not used
        return "the check could not run"


def _item(value: Any, limit: int = ITEM_CHARS) -> str:
    """One short plain item from the model, or "" (a flagged or empty item is dropped)."""
    if not isinstance(value, str):
        return ""
    text = strip_hidden(value)
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text)
    text = " ".join(text.split()).strip(" -*\u2022,")
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    if not text or _ADDRESS.search(text) or flag(text):
        return ""
    return text


def _items(value: Any, most: int, goal: str = "") -> list[str]:
    """Up to *most* items. With *goal*: an item that is only one or two of the goal's own words is dropped."""
    out: list[str] = []
    said = _words(goal)
    for v in value if isinstance(value, list) else []:
        got = _item(v)
        mine = _words(got)
        if goal and len(mine) <= 2 and mine <= said:
            continue
        if got and got.lower() not in {x.lower() for x in out}:
            out.append(got)
        if len(out) >= most:
            break
    return out


def render(answer: Any, goal: str = "") -> str:
    """The brain's JSON answer as plain text, or "" when it gave no usable role. The "won't" lines are the
    Core's own (:data:`FLOOR_WONT`); the model writes none."""
    if not isinstance(answer, dict):
        return ""
    role = _item(answer.get("role")).rstrip(".")
    role = re.sub(r"(?i)^role\s*:\s*", "", role)
    if not role:
        return ""
    lines = [f"Role: {role}."]
    helps = _items(answer.get("can_help") or answer.get("knows"), MAX_KNOWS, goal)
    if helps:
        lines.append(HELPS_HEAD)
        lines += [f"- {k}" for k in helps]
    talks = _item(answer.get("talks"), 240)
    if talks:
        lines.append(f"How it talks: {talks[0].upper() + talks[1:]}")
    questions = [q if q.endswith("?") else q.rstrip(".") + "?"
                 for q in _items(answer.get("first_questions"), QUESTIONS)]
    if questions:
        lines.append("First questions it asks:")
        lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
    lines.append("What it won't do:")
    lines += [f"- {w}" for w in FLOOR_WONT]
    text = "\n".join(lines)
    return text[:MAX_CHARS] if not flag(text) else ""


# -- reading the text back (hers may be edited) ---------------------------------------------------
def role_of(text: str) -> str:
    m = re.search(r"(?im)^\s*role\s*:\s*(.+?)\s*$", str(text or ""))
    return m.group(1).rstrip(".") if m else ""


def _section(text: str, head: str, item: str) -> list[str]:
    out, inside = [], False
    for line in str(text or "").splitlines():
        if re.match(head, line.strip(), re.I):
            inside = True
            continue
        if not inside or not line.strip():
            continue
        m = re.match(item, line.strip())
        if not m:
            break
        out.append(m.group(1).strip())
    return out


def knows_of(text: str) -> list[str]:
    return _section(text, r"^(?:knows(\s+well)?|what it can help with)\s*:?$", r"^[-*\u2022]\s+(.+)$")


def questions_of(text: str) -> list[str]:
    return _section(text, r"^first questions", r"^\d+[.)]\s+(.+)$")


# -- writing it ----------------------------------------------------------------------------------
Ask = Callable[[str, str], tuple[Any, str]]


def ask_user(goal: str, jobs: list[str] | None = None, left_out: list[str] | None = None) -> str:
    text = f"Goal: {' '.join(str(goal or '').split())}"
    names = [str(j) for j in jobs or [] if str(j).strip()]
    if names:
        text += "\nJobs (the only things this bot does): " + "; ".join(names[:12])
    left = [str(x) for x in left_out or [] if str(x).strip()]
    if left:
        text += "\nNot a job yet (never list these): " + "; ".join(left[:6])
    return text


def _not_promised(answer: Any, jobs: list[str] | None, left_out: list[str] | None) -> Any:
    """The answer without a "can help" item about something left out of the jobs (D84): an item that shares a
    word with a part not made a job, and none with any job, is dropped, so the persona never promises it."""
    left = [_words(x) for x in left_out or [] if str(x).strip()]
    if not isinstance(answer, dict) or not left:
        return answer
    said: set[str] = set()
    for j in jobs or []:
        said |= _words(j)
    key = "can_help" if answer.get("can_help") is not None else "knows"
    items = answer.get(key)
    if not isinstance(items, list):
        return answer
    kept = [v for v in items if not (any(_words(str(v)) & w for w in left) and not (_words(str(v)) & said))]
    return {**answer, key: kept}


def write_from_goal(state: Path | str, goal: str, build: str | None, ask: Ask,
                    jobs: list[str] | None = None, left_out: list[str] | None = None) -> dict:
    """Ask the brain once and keep the persona. *ask(system, user) -> (json or None, built_with)*. *jobs*: the
    jobs as kept, the only things it says it can help with; *left_out*: what she asked for that is not a job,
    never promised (D84). Returns the record, or ``{"text": "", "said": <why>}`` when nothing usable came back
    (nothing is kept)."""
    try:
        answer, built_with = ask(ASK_SYSTEM, ask_user(goal, jobs, left_out))
    except Exception:  # noqa: BLE001 - never a veto on the plan: said plainly
        answer, built_with = None, ""
    text = render(_not_promised(answer, jobs, left_out), goal)
    if not text:
        return {"text": "", "said": NOT_WRITTEN}
    return save(state, {"text": text, "source": "brain", "build": build, "goal_sha": goal_sha(goal),
                        "built_with": str(built_with or ""), "written_at": _now()})


NOT_WRITTEN = ("The brain did not write who your bot is this time. You can write it yourself in Settings, "
               "or ask for it again there.")


def needs_writing(state: Path | str, goal: str) -> bool:
    """True when there is no persona for this goal yet (hers, or the brain's, for the same goal, is kept)."""
    rec = load(state)
    return not rec.get("text") or rec.get("goal_sha") != goal_sha(goal)


def set_text(state: Path | str, text: Any) -> dict:
    """Her own words for the persona. Raises ValueError with the plain reason."""
    problem = control_problem(text)
    if problem:
        raise ValueError(problem)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("Write who your bot is, or ask for it again from your goal.")
    if len(text) > MAX_CHARS:
        raise ValueError(f"Keep it to {MAX_CHARS} characters or fewer (this is {len(text)}).")
    rec = load(state)
    rec.update(text=text, source="owner", written_at=_now())
    return save(state, rec)


def view(state: Path | str) -> dict:
    """What ``GET /v1/settings/persona`` answers."""
    rec = load(state)
    text = rec.get("text") or ""
    why = flag(text) if text else ""
    in_use = "none" if not text else ("plain default" if why else "persona")
    questions = questions_of(text)
    return {"text": text, "source": rec.get("source") or "none", "build": rec.get("build"),
            "goal_sha": rec.get("goal_sha"), "built_with": rec.get("built_with") or "",
            "written_at": rec.get("written_at"), "role": role_of(text), "knows": knows_of(text),
            "first_question": questions[0] if questions else "", "flag": why, "in_use": in_use,
            "max_chars": MAX_CHARS}


# -- what the chat uses --------------------------------------------------------------------------
def in_use(state: Path | str) -> str:
    """The persona text the chat uses: hers, the plain default when the screen flagged it, or ""."""
    text = load(state).get("text") or ""
    if not text:
        return ""
    return PLAIN_DEFAULT if flag(text) else text


def first_question_due(state: Path | str) -> str:
    """The persona's first question when the first chat since it was written has not happened yet."""
    rec = load(state)
    text = rec.get("text") or ""
    if not text or rec.get("opened_at") or flag(text):
        return ""
    questions = questions_of(text)
    return questions[0] if questions else ""


def mark_opened(state: Path | str) -> None:
    rec = load(state)
    if rec.get("text") and not rec.get("opened_at"):
        rec["opened_at"] = _now()
        save(state, rec)


def system_text(listed: str, rules: str, *, role: str = "", call_me: str = "", about: str = "",
                opening: str = "", skills: str = "") -> Optional[str]:
    """The chat's system message with the persona and her notes on top and the rules last, or None when
    there is nothing to add (the message stays exactly as it was)."""
    top: list[str] = []
    if role:
        top.append(ROLE_HEAD + "\n" + role)
    if call_me:
        top.append(f"The owner likes to be called {call_me}. Use it when you greet them.")
    if about:
        top.append(ABOUT_HEAD + "\n" + about)
    if opening:
        top.append("This is the owner's first chat since setup. Answer what they said, then ask them this question, as "
                   f"written: {opening}")
    if skills:
        top.append(skills)                       # WritersBot: an installed skill the turn needs; the rules still win
    if not top:
        return None
    return "\n\n".join(top + [listed, rules, RULES_WIN])


def plain_said(text: str) -> str:
    """Words for comparing: lower case, letters and digits only."""
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


__all__ = ["ASK_SYSTEM", "FLOOR_WONT", "MAX_CHARS", "NOT_WRITTEN", "PLAIN_DEFAULT", "RULES_WIN", "first_question_due",
           "flag", "goal_sha", "in_use", "load", "mark_opened", "needs_writing", "questions_of", "render",
           "role_of", "set_text", "system_text", "view", "write_from_goal"]
