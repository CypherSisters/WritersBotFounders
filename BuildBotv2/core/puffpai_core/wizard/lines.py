"""Every line the wizard engine says that is not in a step record (03 1.3, 1.4).

Both renderers show these as they come; neither has wording of its own
(03 1.6). Some lines are kept from v1 ``wizard/wording.py`` at
``PuffPAIBuildBot@63c8a037`` (the first look, the goal help); the shown-once
lines replace v1's untrue "gone from the screen and from memory".
"""

import re
from datetime import datetime, timezone

PRIVACY = "Nothing leaves your computer without your consent."

# D122: Stop BuildBot during setup, and a question that was answered somewhere else.
SETUP_STOPPED = "Stop BuildBot ended setup here. Your finished steps are saved, and setup goes on from there."
ANSWERED_ELSEWHERE = "That question was answered somewhere else, or is no longer needed, so setup goes on here."

# 03 1.3
SHOWN_ONCE = ("This code is shown once. BuildBot does not keep it, so it can't show it to you again. "
              "Write it down now.")
# The terminal never says it cleared the screen: a console may ignore the request, and a terminal or SSH app
# keeps its own scroll history (D36(c); v1 15bad54b secure_vault.py:197-198, zone_vault.py:810-814).
SHOWN_ONCE_TERMINAL = (SHOWN_ONCE + " Your terminal may still have it in its scroll history, so clear that "
                       "history or close this window when you are done.")
NOT_CLEARED = ("BuildBot could not clear this window, so the code may still be on it. Close this window or clear "
               "it yourself when you are done.")
CLEARED = {
    "terminal": ("That matches. You have a way back in to your vault. Your terminal or SSH app may still have the "
                 "code in its scroll history, so clear it or close this window when you are done."),
    "browser": "BuildBot removed the code from this page. A screenshot or a printed copy is yours to keep safe.",
}
PIN_MISMATCH = "Those two PINs are not the same, so nothing was set. Pick your PIN again, then type it again."
PIN_AGAIN_LOST = "BuildBot no longer has the PIN you picked (setup was restarted). Pick your PIN again."
CODE_MISMATCH = "That does not match the code shown. Type it again, or type new for a new code."
CODE_LOST = "If you did not write the code down, type new and BuildBot makes a new one."
NEW_CODE_ASK_PIN = "To make a new code, type your vault's PIN. The code you were shown works until the new one is made."
NEW_CODE_ASK_PIN_LOCKED = ("Your vault is locked. To make a new code, type your vault's PIN: it opens your vault too. "
                           "The code you were shown works until the new one is made.")
NEW_CODE_WRONG_PIN = "That is not your vault's PIN, so no new code was made. Type the code, or type new to try again."
NEW_CODE_LOCKED = ("Your vault is locked, so BuildBot can't make a new code now. Open it with your PIN first (Vault on "
                   "the Desktop, or /vault unlock in the chat), then type new again.")
NEW_CODE_NOT_SAVED = ("BuildBot could not save a new code, so nothing changed and the code you were shown still works. "
                      "Type new to try again.")
NEW_CODE_NOT_SAVED_OPENED = ("Your vault is open now, but BuildBot could not save a new code. The code you were shown "
                             "still works.")
NEW_CODE_NO_VAULT = "BuildBot can't find your vault's list of ways in, so it can't make a new code."
NEW_CODE_UNREADABLE = "Your vault's list of ways in can't be read, so BuildBot can't make a new code."
NEW_CODE_SAFE_MODE = ("Your vault's list of ways in was changed outside PuffPAI, so it is in safe mode, and no new code "
                      "was made here. To repair it, open Vault on the Desktop and make a new recovery code there.")
OLD_CODE_GONE = "Your old code no longer works. This is your new code."
SETUP_DONE_VAULT = "Setup is already done. Open your vault on the Desktop or with /vault unlock in the chat."
CANT_READ = "BuildBot could not read that. Type it again."

# 03 1.4. {done}, {size} and {reason} are filled in.
BRAIN = {
    "not_downloaded": ("Your bot's brain is still downloading ({done} of {size} GB), so this list comes from "
                       "your own words. You can add more below."),
    "not_downloaded_plain": ("Your bot's brain is not downloaded yet, so this list comes from your own words. "
                             "You can add more below."),
    "loading": ("Your bot's brain is still starting up, so this list comes from your own words. "
                "You can add more below."),
    "timed_out": ("Your bot's brain did not answer in time, so this list comes from your own words. "
                  "You can add more below."),
    "error": ("Your bot's brain could not answer ({reason}), so this list comes from your own words. "
              "You can add more below."),
}
NO_BRAIN_SET = "no brain is picked yet"

FIRST_LOOK = ("Before any questions, here is what BuildBot found on this computer. "
              "Nothing has been downloaded or changed.")

# The Done step says what really happened, job by job (never "set up" when nothing works).
DONE_PLAIN = ("Your bot is set up. The Desktop shows everything installed, and the chat box works on all of it "
              "at once.")
DONE_ALL = "Your bot is set up, and every job works:"
DONE_SOME = "Setup is finished, but not every job works yet:"
DONE_NONE = "Setup is finished, but no job works yet:"
DONE_READING = "Setup is finished, but no job has an app yet. Your library has some reading:"
DONE_UNREAD = ("Setup is finished, but BuildBot could not read how your bot's jobs ended, so it can't say they "
               "work. The Desktop shows each one.")
DONE_NEXT = ("The Desktop shows what is installed. To try a job again, ask the chat to add it, or paste a GitHub "
             "address for it.")


#: How a job ended or where it is, in the owner's words (never the raw state word).
JOB_END = {"working": "works", "not_yet": "not working yet", "nothing_found": "nothing found",
           "building": "Toolsmith is still working on it", "planned": "not started yet",
           "waiting_yes": "waiting for your Yes", "waiting_you": "waiting for your step on the Checklist",
           "waiting_vault": "waiting for you to unlock your vault", "held": "held, not done"}
# A plan card that got no Yes (lane I6b): its line says why ("You said No, so nothing was built.").
JOB_END.update({"said_no": "stopped at the plan", "no_answer": "stopped at the plan",
                "replaced": "replaced by a newer plan"})
#: A job with no app, only some reading in the library (D123): it never says "works".
JOB_LIBRARY = "no app yet, your library has some reading on this"
# A free site is a job's answer, and nothing was installed (D121): never counted as a working app.
JOB_END["free_site"] = "no app needed"
BUILD_ENDED = "Every job has ended. The next step says how each one went."


def still_line(job: dict, at: datetime | None = None) -> str:
    """A long step's sign of life in the setup terminal (D113), from what the job already says: its "now", and how
    long it has been in this state. The Desktop's own is ``stillWorking`` in its logic.js."""
    what = str(job.get("name") or "A job")
    now = str(job.get("now") or "").strip().rstrip(".")
    if now:
        what += f": {now}"
    try:
        since = datetime.fromisoformat(str(job.get("since") or ""))
        minutes = int(((at or datetime.now(timezone.utc)) - since).total_seconds() // 60)
    except (TypeError, ValueError):
        return f"Still working: {what}."
    if minutes < 1:
        return f"Still working: {what}."
    return f"Still working: {what}, {minutes} minute{'' if minutes == 1 else 's'} so far."


def _state(r: dict) -> str:
    return str(r.get("state") or "").split(":")[0]


def _tried(r: dict) -> list:
    return [t for t in r.get("tried") or [] if isinstance(t, dict) and t.get("name")]


def _why(t: dict) -> str:
    """One tried app's reason, without its name said twice ("Mataroa: Mataroa: You said No ...")."""
    why = str(t.get("why") or "it did not work").strip()
    name = str(t["name"])
    while why.startswith(name + ":"):
        why = why[len(name) + 1:].strip()
    return why.rstrip(".") + "."


def _nothing_why(reason: str) -> str:
    rest = re.sub(r"^Nothing found yet[:.]?\s*", "", str(reason or "")).strip()
    return rest or "Toolsmith found no app for it."


def _said(text: str) -> str:
    """A line that ends as a sentence does (a job's line can be a bare app name, "building: RecipeSage")."""
    text = str(text or "").rstrip()
    return text if not text or text[-1] in ".!?:" else text + "."


def _first(reason: str) -> str:
    """The first sentence of a reason, for the short Done line."""
    text = str(reason or "").strip()
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text).strip()


#: The part of a job's line that says it stopped at its share of the cap (``find.share_line``, D119).
_SHARE = re.compile(r"^(Toolsmith stopped trying apps for this job:.*? still stops at \$[\d.]+\.)( Not tried: [^:]*?\.(?=\s|$))?")


def _share(reason: object, whole: bool = True) -> str:
    """That part of a job's line, or "" (*whole*: with the apps it did not try)."""
    m = _SHARE.match(str(reason or ""))
    return "" if not m else (m.group(0) if whole else m.group(1))


def job_result_lines(results: list) -> list:
    """Each job's end, short and plain (the Done step, chat): works, not working yet: why, nothing found: why.
    The Prove step above it has the long version (:func:`prove_lines`)."""
    out = []
    for r in results or []:
        state, name = _state(r), r["name"]
        if r.get("kind") == "knowledge":           # not a job: what was read, in its own words (lane I11)
            out.append(_said(f"{name}: {r.get('reason') or JOB_END.get(state, 'not read yet')}"))
            continue
        if state == "working" and r.get("library"):
            out.append(f"{name}: {JOB_LIBRARY}.")
        elif state == "working":
            out.append(f"{name}: works.")
        elif state == "not_yet" and _share(r.get("reason")):
            out.append(f"{name}: not working yet: {_share(r.get('reason'), whole=False)}")
        elif state == "not_yet":
            tried = _tried(r)
            if len(tried) == 1:
                why = f"{tried[0]['name']}, the app Toolsmith tried, does not work yet"
            elif tried:
                why = f"none of the {len(tried)} apps Toolsmith tried works yet"
            else:
                why = _first(r.get("reason")).rstrip(".") or "no app worked yet"
            out.append(f"{name}: not working yet: {why}.")
        elif state == "nothing_found":
            out.append(f"{name}: nothing found: {_nothing_why(r.get('reason'))}")
        else:
            reason = f" {r['reason']}" if r.get("reason") else ""
            out.append(_said(f"{name}: {JOB_END.get(state, 'not working yet')}.{reason}"))
    return out


def prove_lines(results: list) -> list:
    """Each job's end with the whole reason (the Prove step): what the brain read from a working app, and
    for a job that does not work yet, each app Toolsmith tried and why it was not used."""
    out = []
    for r in results or []:
        state, name = _state(r), r["name"]
        reason = str(r.get("reason") or "").strip()
        if r.get("kind") == "knowledge":           # not a job: what was read, in its own words (lane I11)
            out.append(_said(f"{name}: {reason or JOB_END.get(state, 'not read yet')}"))
            continue
        if state == "working" and r.get("library"):
            out.append(_said(f"{name}: {reason or JOB_LIBRARY}"))
        elif state == "working":
            out.append(_said(f"{name}: works." + (f" {reason}" if reason else "")))
        elif state == "not_yet" and _tried(r):
            tried = _tried(r)
            out.append(f"{name}: not working yet. Toolsmith tried {', '.join(t['name'] for t in tried)}:")
            out += [f"{t['name']}: {_why(t)}" for t in tried]
            out += [x for x in (_share(reason),) if x]
        elif state == "nothing_found":
            out.append(f"{name}: nothing found. {_nothing_why(reason)}")
        else:
            word = JOB_END.get(state, "not working yet")
            out.append(_said(f"{name}: {word}." + (f" {reason}" if reason else "")))
    return out


#: What a Yes does on a build card, at that moment (the card's own lines say the rest). A hold card comes
#: before anything of the app runs; an install card after Toolsmith got it working.
CARD_HELP = {"hold": "Your Yes lets Toolsmith try it; nothing from it runs before that.",
             "install": "Toolsmith got this app working. Your Yes adds it to your bot.",
             "store": "Your Yes adds it to your bot before the build. Nothing of it runs before you answer.",
             "": "Your Yes does what this card says. Nothing on it happens before you answer."}

GOAL_WORKING = ("Your bot's brain is turning your goal into jobs. The first answer can take a few minutes while "
                "the model loads.")
GOAL_STILL = "Still turning your goal into jobs ({seconds} seconds so far)."
PLAN_WORKING = ("Toolsmith is making the plan: it looks at what is already on your bot, then searches the places "
                "you said Yes to and reads READMEs. This can take a few minutes.")

# The Done step's one line about more apps (lane I10c: the wizard lists no apps; the store is on the Desktop).
PLAN_NEEDS_KEY = "Toolsmith plans with your cloud model, and its key is in your vault, so the plan waits until the vault is open."
STORE_MORE = "More apps are in the PuffPAI store on your Desktop."
# A PuffPAI store app she ticked on the plan card, added before the build with its own Yes card.
STORE_TICK_NOTE = "Your tick adds it first, with its own Yes card, instead of searching."
#: Lane I17: setup picks one chat model; the Done step says where the others are added.
MODELS_LATER = "You can add a coding or picture model later on the Developer page."
MODEL_TICK_NOTE = "Your tick downloads it first, with its own Yes card, then the build starts."
STORE_LATER = "You can add it later from the PuffPAI store on your Desktop."
STORE_FIRST = ("The PuffPAI store apps you ticked are added first, each with its own Yes card. "
               "Toolsmith starts the build when they are done.")

# The name and look steps (lane I10c).
IDEAS_WORKING = "Your bot's brain is thinking of names and a look from your goal."
IDEAS_STILL = "Still thinking of names ({seconds} seconds so far)."
NAMES_FROM_GOAL = "Your bot's brain suggested these names from your goal."
NAMES_DEFAULT = "Your bot's brain could not suggest names this time, so the name is Puff unless you type another."
LOOK_PICKED = "Your bot's brain suggests {name} for your goal. {default} is the default; pick any look you like."
LOOK_SAVED = "Your Desktop wears {name}. You can change it later in Looks."

OLLAMA_FOUND = "Ollama is running on this computer at {address}. Your bot's brain uses it."
OLLAMA_NOT_FOUND = ("Ollama is not running on this computer (BuildBot asked 127.0.0.1 for it). Start Ollama, or "
                    "install it from ollama.com, then run this step again.")

NOT_BUILT = "This part of BuildBot is not built yet, so this step waits for it."
NEEDED = "This one is needed."
PICK_ONE = "Pick one of the choices shown."
YES_OR_NO = "Type yes or no."
A_NUMBER = "Type a number, like 5."

HINT = {
    "terminal": {
        "choice": "Type a number, or press Enter for {default}.",
        "choice_nodefault": "Type a number.",
        "confirm": "Type yes or no.",
        "skip": "Press Enter to skip.",
        "use": "Press Enter to use {default}.",
        "suggest": "Type a number or your own, or press Enter for {default}.",
        "hidden": "What you type is hidden.",
    },
    "browser": {
        "choice": "", "choice_nodefault": "", "confirm": "", "skip": "Leave it empty to skip.",
        "use": "Leave it as it is to use {default}.", "hidden": "",
        "suggest": "Leave it as it is to use {default}, or type your own.",
    },
}

SHOWN_SECRET = "Saved, not shown"
#: An optional key left empty (D93): nothing was taken or kept.
SHOWN_SKIPPED = "Skipped"
SHOWN_ONCE_DONE = "Shown once"
