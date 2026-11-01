r"""``puffpai`` with no words: the bot's chat in this terminal (D86, P0 of docs/12_CLI_FIRST_STUDY.md).

A thin client of the running Core's API, with the terminal key the Core writes at every start
(``settings\terminal.json``, ``cli._terminal``). It adds no Core part and no second answer path:

- Chat is ``POST /v1/chat/stream``, the Desktop's own route, in the Desktop's conversation ("main"), so
  Concierge, ``ask_toolsmith``, D77 joining and Yes cards behave exactly as there.
- A Yes card is drawn from ``GET /v1/consent?status=pending`` (the Core's own ``title`` and ``lines``, what the
  Desktop shows) and answered with ``POST /v1/consent/<id>/answer`` and that card's ``card_sha``, so a card
  that changed is refused (D57). A floor-level card (D29) takes the PIN, never one key. A plan Yes while the
  build needs its cloud key and the vault is locked asks for the PIN in place and opens the vault first
  (D68, ``POST /v1/vault/unlock``), as the Desktop's ``keysReady`` does.
- The PIN is read hidden (``getpass``), sent once in a request body, and never printed, kept, logged or put
  in an error line. Python can't wipe a string from memory; it is dropped as soon as the request is sent.
- It never starts the Core. With none running it says so and how to start it (D86: a Core started hidden by
  a chat window would stop when that window closes, D18, or outlive it unseen).
- ``/dashboard``, and plain words that clearly ask for it ("open the dashboard"), open the Desktop in her
  browser with a one-time pair link, as ``puffpai pair`` makes it (D94). ``/window``, and plain words that clearly
  ask for it, open the PuffPAI window (D81). Nothing else she types is caught here.
- The brain writes markdown. ``Markdown`` draws it as it streams and never shows its markers (D86). A Yes card's
  lines are the Core's own words and stay exactly as they came (D57).

Words are plain, and every color comes with words (never color alone). ``NO_COLOR`` turns color off. Box
lines are Unicode where the output can show them, else ASCII (the old Windows console). The D76/D81 look
colors (``--term-bg``, ``--term-fg``) are not used yet: plain is the default.
"""

from __future__ import annotations

import difflib
import getpass
import http.client
import json
import os
import re
import shutil
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable, Iterator, TextIO

# -- words ---------------------------------------------------------------------------------------------------
NOT_RUNNING = "BuildBot is not running, so there is no bot to chat with yet."
HOW_TO_START_CMD = "Start it first: {how}, then run this again."
HOW_TO_START = "Start it first (in another terminal), then run this again: {serve}"
GONE = "BuildBot stopped answering. Start it again, then open this chat again."
TYPE_TO_CHAT = "Type to chat. / for commands."
#: The chat the window's Terminal button opened this one in (D115): only ``cli.open_terminal`` sets it.
CONVERSATION_ENV = "PUFFPAI_CHAT"
RECENT_LINES = 6
RECENT_CHARS = 280
RECENT_SAID = "The same chat as your window. Its last words:"
PROVE_TIMEOUT_S = 300
PROVE_ASK = "Reply with the one word OK."
OUTSIDE_LINES = 20
ONCE_CARD = "A Yes card waits on Your turn. Nothing was answered, and this command takes no Yes."
#: The seven agents, as the Desktop's Agent Team says them (logic.js AGENTS).
AGENTS = (
    ("archivist", "Archivist", "Holds every key and your vault. Lends a key for one use; never hands it over."),
    ("concierge", "Concierge", "Runs each chat turn: picks the tools, asks for your Yes when a tool changes something."),
    ("steward", "Steward", "Starts and stops every app, each in its own box, on this computer only."),
    ("caretaker", "Caretaker", "Checks each app's health, restarts one that failed, and puts BuildBot in safe mode when a check fails."),
    ("majordomo", "Majordomo", "Keeps your Your turn list and, later, your saved routines and reminders."),
    ("drawbridge", "Drawbridge", "Checks everything that comes in and every change a tool makes, by fixed rules. No answer means no."),
    ("toolsmith", "Toolsmith", "Finds ready-made apps for what you want, tries them in a folder of their own, and proves they work."),
)
#: Why, as the Desktop says it (logic.js WHY).
LEDGER_WHY = {
    "bridge_closed": "the Bridge was closed",
    "switched_off": "that switch is off",
    "not_on_list": "that host is not on its list",
    "blocked_by_provider": "a block list said no",
    "labelled": "it carried a private detail",
    "needs_yes": "it needed your Yes",
    "granted": "you said Yes to it",
    "inside_address": "it pointed inside your network",
    "network_filter": "a network filter said no",
    "cannot_look_up": "the name could not be looked up",
    "no_answer": "nothing answered",
    "not_encrypted": "it was not encrypted",
    "not_allowed": "it was not allowed",
    "bad_answer": "the answer was broken",
    "too_big": "it was too big",
    "bad_credential": "a sign-in was wrong",
    "lan_only": "it may only reach your home network",
    "around_the_bridge": "it tried to go around the Bridge",
    "drawbridge_down": "Drawbridge was not running",
    "policy_damaged": "the Bridge's settings were damaged",
    "probe": "a check by BuildBot",
}
BYE = "Bye. BuildBot keeps running."
PRIVACY = "Nothing leaves your computer without your consent."
PIN_PROMPT = "Your PIN (hidden): "
PIN_ARG = "Type /unlock alone. Your PIN is asked for next, hidden, and is never shown or kept."
NEEDS_PIN = "This card needs your PIN. One key is not enough for it."
NO_DETAIL = "This card came with no details, so only No is offered here."
#: ``/vault recover`` (D133, v1's ``_vault_recover``): the Vault page's words where it has them. Nothing typed after the
#: command is used, and it is never said back.
RECOVER_ARG = "Type /vault recover alone. What you typed after it was not used, shown or kept."
VAULT_ARG = "Type /vault, /vault unlock, /vault lock or /vault recover, each alone. What you typed was not used, shown or kept."
#: A plain line that starts like a vault command and looks as if it holds the secret (``unlock 1234``): not a chat turn.
SECRET_NOT_SENT = ("That may hold your PIN or a code, so it did not go to your bot, and it was not shown or kept. /unlock and "
                   "/vault recover ask for them hidden. To ask your bot about this, put it another way.")
#: What the route said if it said nothing: ``PUT /v1/toolsmith/keys/<provider>`` saves (200) or seals while locked (202).
KEY_KEPT = {200: "Saved in your vault.", 202: "Sealed in your vault's inbox. It is filed the next time you unlock."}
RECOVER_INTRO = ("Get back in with your recovery code, for a forgotten PIN. You get a new recovery code too.",
                 "Nothing you type here is shown or kept.")
RECOVER_CODE_PROMPT = "Your recovery code (hidden): "
RECOVER_PIN_PROMPT = "A new PIN (hidden): "
RECOVER_AGAIN_PROMPT = "Type the new PIN once more (hidden): "
RECOVER_BACK_PROMPT = "Type the new code back to check it, or press Enter to skip (hidden): "
RECOVER_NO_VAULT = "There is no vault on this computer yet, so there is nothing to recover. Run puffpai setup to make one."
RECOVER_NO_CODE = "No recovery code was typed. Nothing was changed."
RECOVER_STILL_WORKS = "Nothing was changed, and your recovery code still works."
RECOVER_MISMATCH = "Those two PINs did not match. " + RECOVER_STILL_WORKS
RECOVER_MAYBE = "If your recovery code is refused now, try your new PIN: it may already open your vault."
RECOVER_DONE = ("Your vault is open again. Your new PIN opens it from now on.",
                "The recovery code you just used and your old PIN no longer work.")
RECOVER_NO_NEW_CODE = "The Core gave no new recovery code. Make one on the Vault page with your new PIN."
RECOVER_NOT_CHECKED = "It was not checked. It opens your vault as it is, and the Vault page can test it."
RECOVER_BACK_WRONG = "That did not match. Check what you wrote down."
RECOVER_BACK_LAST = ("That did not match, so check what you wrote down against the code above. It still opens your "
                     "vault. If you can't read it, make a new one on the Vault page with your new PIN.")
RECOVER_BACK_TRIES = 3
UNLOCK_FOR_YES = ("Your vault is locked. Type your PIN: your Yes opens it, and Toolsmith keeps the key it needs "
                  "until this build ends.")
UNLOCK_WAITING = "Toolsmith is waiting for your vault. Press u, then type your PIN; it stays hidden."
PIN_NEVER = "Never type your PIN after a command; BuildBot asks for it hidden."
ONLY_KEYS = "Only u or Enter work here. BuildBot did not use, show or send what you typed."
UNLOCK_TYPE_PIN = "Type your PIN first. Nothing was answered yet."
CHANGED_AGAIN = "That card changed after you saw it, so your answer was not taken. Here it is again:"
ANSWERED_ELSEWHERE = "That card is not waiting any more (it was answered or it ended)."
WATCH_STOPPED = "Stopped watching. The build keeps going; /watch follows it again."
EXPLAIN = ("A Yes card asks your Yes for exactly what it lists, nothing more. y says Yes; n says No, and nothing "
           "is done. If the card changes before you answer, your answer is refused and you see it again. You can "
           "answer here or on the Desktop: the first answer counts.")
EXPLAIN_FLOOR = ("This card is floor-level: private data leaving, a change to a real device, a provider that reads "
                 "raw data, or relaxing a label. It needs your PIN, so no program can say Yes for you. Windows "
                 "Hello is not built yet.")
EXPLAIN_TICKS = "e lets you tick what the card offers before you say Yes. Nothing ticked: it goes as planned."
CHAT_STEP_WORDS = {"running": "working", "done": "done", "failed": "did not work"}

#: The Core's job states (03 3.6) in plain words: the Desktop's own list (logic.js JOB_WORDS).
JOB_WORDS = {"planned": "planned", "waiting_yes": "waiting for your Yes", "waiting_you": "waiting for you, see /turn",
             "waiting_vault": "waiting for your vault to be unlocked", "held": "held: Drawbridge set an app "
             "aside", "building": "building", "working": "working", "not_yet": "not working yet",
             "nothing_found": "nothing found yet", "said_no": "you said No", "no_answer": "no answer in time",
             "free_site": "a free site does this, nothing installed"}
#: A job with no app, only some reading in the library (D123): `working` to the Core, never to her.
_LIBRARY_SAID = "your library has some reading on this"
JOB_LIBRARY = "no app yet, " + _LIBRARY_SAID
ACTIVE_JOBS = ("planned", "waiting_yes", "waiting_you", "waiting_vault", "building")
#: A build line ending like this never gets a check mark.
_STEP_BAD = tuple(JOB_WORDS[s] for s in ("held", "not_yet", "nothing_found", "said_no", "no_answer"))
VAULT_WORDS = {"open": "open", "locked": "locked", "not_set_up": "not set up yet", "missing": "can't find its store",
               "throttled": "locked: too many tries, wait a moment", "safe_mode": "in safe mode"}

#: The commands, defined once: /help, the search and the did-you-mean all read this. (name, args, what it does)
COMMANDS = [
    ("build", "<goal>", "Toolsmith plans it, you see one plan card, then say Yes or No"),
    ("plan", "[build]", "Show the plan that waits for your Yes again"),
    ("yes", "[card]", "Show a waiting Yes card and answer it"),
    ("no", "[card]", "Say No to a waiting Yes card"),
    ("watch", "[build]", "Follow a build until it ends or needs you (Ctrl+C stops watching, not the build)"),
    ("turn", "[done|skip <n>]", "Your turn: steps for you, and Yes cards that wait"),
    ("apps", "", "Installed apps, their status and health"),
    ("open", "<app>", "Open an app in your browser, in a new tab"),
    ("bridge", "[open|close]", "Is the Bridge open or closed"),
    ("vault", "[unlock|lock|recover]", "Your vault: open or locked. recover gets you back in with your recovery code"),
    ("unlock", "", "Open your vault with your PIN (typed hidden)"),
    ("lock", "", "Lock your vault now"),
    ("brain", "[model]", "Which local model answers here; a name changes it"),
    ("agents", "", "The seven agents, and what each is doing now"),
    ("outside", "[log [n]]", "The Bridge, and what tried to leave your computer"),
    ("keys", "[add|remove <company>]", "API keys and Toolsmith's cloud model: which are saved, never a value"),
    ("prove", "", "Ask the running bot to prove it works, with one real chat turn"),
    ("dashboard", "", "Open your dashboard in your browser"),
    ("window", "", "Open the PuffPAI window"),
    ("clear", "", "Clear this window (the bot still remembers the chat)"),
    ("help", "[word]", "This list, or search the commands by any word"),
    ("quit", "", "Leave the chat (BuildBot keeps running)"),
]
NOT_YET = ["add", "know", "link", "people", "settings", "github"]
#: What a not-yet command will do, so the search can find it too.
NOT_YET_WHAT = {"add": "Add a package from a folder or GitHub address", "know": "Your knowledge: add, link or find",
                "link": "Link a folder so your bot can read it", "people": "The people who use this bot",
                "settings": "Your bot's settings",
                "github": "Link your GitHub key, or add code from GitHub"}
#: Extra search words for every command, so /pass finds /vault. The search says back a word only when it is in
#: this table or the lines above, so these stay free of digits: "/1234" can never be said back.
COMMAND_WORDS = {
    "build": "add make create install get new app tool goal toolsmith", "plan": "card review ticks toolsmith waiting",
    "yes": "answer approve accept agree consent card", "no": "answer refuse reject decline deny consent card",
    "watch": "follow progress status live build", "turn": "checklist steps todo reminders tasks waiting",
    "apps": "installed packages list running status health", "open": "launch start show browser tab page",
    "bridge": "internet network online offline web", "vault": "password passcode pin secrets keys safe recover forgot lost code",
    "unlock": "password passcode pin vault", "lock": "password passcode pin vault close secure",
    "agents": "team concierge steward drawbridge archivist majordomo caretaker toolsmith",
    "outside": "bridge ledger log leave left internet network host refused", "keys": "api key token secret cloud github vault",
    "prove": "check test working health proof", "brain": "model llm ollama local", "dashboard": "desktop browser home page web pair",
    "window": "app desktop pane show", "clear": "cls wipe screen reset", "help": "commands search find list",
    "quit": "exit bye leave close stop", "add": "package install source folder github",
    "know": "knowledge documents files notes search", "link": "folder index files documents",
    "people": "users family contacts", "settings": "options preferences config name look",
    "github": "git repo key token",
    }
#: D94: plain words that clearly mean /dashboard ("open the dashboard", "Show me my dashboard!"), and only those.
DASHBOARD_WORDS = re.compile(r"(open|show)( me)?( the| my)? dashboard[.!]?")
#: D81: the same for /window.
WINDOW_WORDS = re.compile(r"(open|show)( me)?( the| my)? window[.!]?")
#: A vault command's word first, whole or with something other than a letter stuck to it (``unlock1234``, ``pin=...``).
_VAULT_START = re.compile(r"\W*(?:unlock|lock|vault|recover|pin)(?![^\W\d_])", re.IGNORECASE)
_LETTERS = re.compile(r"[^\W\d_]+")


def holds_a_secret(line: str) -> bool:
    """A plain line that starts like a vault command and has a digit or more than three words in it. A hyphen or an
    equals sign parts words here, since ``hazel-otter-river`` is a PIN. "How do I unlock my vault" and "unlock my vault"
    are not this. It can't catch a secret in a short line of letters (``unlock hazelotter``) or later in a sentence."""
    if not _VAULT_START.match(line):
        return False
    return any(c.isdigit() for c in line) or max(len(line.split()), len(_LETTERS.findall(line))) > 3
NO_WINDOW = "The window is not on this bot yet."
#: The Concierge's own tools (zone2/concierge.py) in plain words. Any other tool is "Working...", so a line here
#: never names a goal or an app.
TOOL_WORDS = {"list_installed": "Looking at what's installed", "open_package": "Looking at what an app can do",
              "search_knowledge": "Searching your knowledge", "read_pinned": "Reading your pinned document",
              "show_passage": "Finding the passage in your document",
              "send_document": "Sending your document",
              "checklist_add": "Adding to Your turn",
              "ask_toolsmith": "Asking Toolsmith", "read_app": "Reading an app's page", "open_app": "Opening an app",
              "arrange_home": "Changing your Home page", "run_command": "Running a command",
              "open_in_program": "Opening a file"}
#: The brain loop's default line (zone2/loop.py) says only the raw tool name, so it counts as no line.
RAW_TOOL_LINE = re.compile(r"Using \S+ \.\.\.")
PAIR_ONCE = "(works once, for 10 minutes)"
NOT_YET_LINE = ("Not yet here (use the Desktop): " + ", ".join("/" + n for n in NOT_YET)
                + ". To add a package from a folder or GitHub address now: puffpai add <source>.")

STREAM_TIMEOUT_S = 900.0          # one read of the chat stream; a plan card waits up to 10 minutes for her
PLAN_TIMEOUT_S = 900.0            # POST /v1/toolsmith/plan: Toolsmith plans for up to 10 minutes
CALL_TIMEOUT_S = 60.0
NEW_CARD_WAIT_S = 5.0             # a card asked for with no id yet (read_app's start card) appears within this
WATCH_EVERY_S = 1.0
#: The Core went away mid-call or mid-stream (a refused or cut connection, a cut reply).
GONE_ERRORS = (OSError, http.client.HTTPException)


# -- the look: Unicode or ASCII, color or none ----------------------------------------------------------------
BOX_UNICODE = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│",
               "dot": "·"}
BOX_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "dot": "|"}
COLORS = {"ok": "32", "warn": "33", "bad": "31", "dim": "2", "bold": "1", "accent": "36", "italic": "3"}


#: Characters never written as they came: control codes (ESC starts a terminal command) and the bidi marks that
#: reorder what is shown. Text from the Core (the brain's words, app names, card lines) can carry them; the
#: Desktop shows text as text, so the terminal does the same by dropping them.
_HIDDEN = {c for c in range(0x20) if c not in (0x09, 0x0A)} | {0x7F} | set(range(0x80, 0xA0)) \
    | set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A)) | {0x200E, 0x200F, 0x061C}
_STRIP = {c: None for c in _HIDDEN} | {0x09: "    "}


def clean(text: Any) -> str:
    """Text from the Core, safe to write to a terminal: no escape codes, no bidi reordering."""
    return str(text).translate(_STRIP)


def _vt_conout() -> bool:
    """Escape codes on for this console's screen, through ``CONOUT$`` opened to read and write. An output
    handle from ``> con`` is write-only, so its mode can't be read; the mode belongs to the screen, so setting it
    here holds for that handle too. False when it can't (the old console): then no color and ASCII boxes."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = wintypes.HANDLE
        h = k32.CreateFileW("CONOUT$", 0xC0000000, 3, None, 3, 0, None)    # read+write, shared, open existing
        if not h or h == wintypes.HANDLE(-1).value:
            return False
        try:
            mode = wintypes.DWORD()
            if not k32.GetConsoleMode(wintypes.HANDLE(h), ctypes.byref(mode)):
                return False
            want = mode.value | 0x0004                                       # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            return want == mode.value or bool(k32.SetConsoleMode(wintypes.HANDLE(h), want))
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:  # noqa: BLE001 - can't tell: plain
        return False


def _can_encode(encoding: str, text: str) -> bool:
    try:
        text.encode(encoding or "ascii")
        return True
    except (LookupError, UnicodeEncodeError):
        return False


class Look:
    """How this window draws: ``color`` only on a terminal that reads escape codes and with no ``NO_COLOR``;
    ``unicode`` box lines only when the output can encode them and (on Windows) the console reads escape codes,
    which the old console does not. Every color comes with words, so nothing is said by color alone."""

    def __init__(self, out: TextIO, env: dict | None = None, tty: Callable[[Any], bool] | None = None,
                 vt_on: Callable[[Any], bool] | None = None) -> None:
        from .wizard.terminal import is_tty, windows_vt_on
        env = os.environ if env is None else env
        self.out = out
        term = (tty or is_tty)(out)
        vt = bool(term and (vt_on or windows_vt_on)(out))
        if term and not vt and vt_on is None:
            vt = _vt_conout()          # "> con" (Chat with BuildBot.cmd) is write-only: ask the screen itself
        self.vt = vt
        self.term = term
        self.color = bool(term and vt and not env.get("NO_COLOR") and env.get("TERM") != "dumb")
        self.encoding = getattr(out, "encoding", None) or "ascii"
        self.unicode = _can_encode(self.encoding, "".join(BOX_UNICODE.values())) and (
            os.name != "nt" or not term or vt)
        self.g = BOX_UNICODE if self.unicode else BOX_ASCII
        marks = self.unicode and _can_encode(self.encoding, "\u2713\u280b")
        self.check, self.doing = ("\u2713", "\u280b") if marks else ("ok", "..")
        cols = shutil.get_terminal_size((80, 24)).columns if term else 80
        self.width = max(40, min(100, cols - 2))

    def paint(self, text: str, kind: str) -> str:
        code = COLORS.get(kind)
        return f"\x1b[{code}m{text}\x1b[0m" if self.color and code and text else text

    def safe(self, text: str) -> str:
        """Text this output can show: what it can't encode becomes "?" (a pipe in an old code page)."""
        return text.encode(self.encoding, errors="replace").decode(self.encoding, errors="replace")

    def write(self, text: str = "", end: str = "\n") -> None:
        self.out.write(self.safe(text) + end)
        self.out.flush()


# -- the brain's markdown --------------------------------------------------------------------------------------
_START_SO_FAR = re.compile(r"`{1,2}|#{1,6}|[-*+]|\d{1,9}[.)]?|>|([-*_])\1* *")
_RULE = re.compile(r"-{3,} *|\*{3,} *|_{3,} *")
_BLOCK = re.compile(r"(#{1,6} )|([-*+] )|(\d{1,9}[.)] )")
_PLAIN = re.compile(r"[^*_`\[\\\n]+")
_LINK = re.compile(r"\[([^\]\n]*)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")
_LINK_SO_FAR = re.compile(r"\[[^\]\n]*(?:\](?:\((?:[^()\s]|\([^()\s]*\)?)*)?)?")
_WAIT, _NONE = -2, -1


class Markdown:
    """The brain's markdown, drawn as it streams (D86): feed it pieces cut anywhere, then call ``end``.

    Each step reads only what more text can't change, and waits otherwise, so the output is the same for every
    way the words were cut. Color on: bold, italic and code as SGR. Color off: the markers just go, the words stay.
    A line's styles end with the line, so ``**`` that never closes can't color the rest of the answer. It holds
    back a marker that may be unfinished; a ``*``, ``_``, backtick or link until its close or the end of the line
    (with no close it was a plain character, as in ``2 * 3``); and a word until its end, to wrap it whole."""

    def __init__(self, look: Look, sink: Callable[[str], Any] | None = None, flat: bool = False) -> None:
        self.look = look
        self.sink = sink or (lambda text: look.write(text, end=""))
        self.color = look.color and not flat
        self.wrap = look.width - 2 if look.term and not flat else 0
        self.buf, self.out = "", []            # text not read yet; what to write next
        self.fresh, self.fence = True, False   # at a line's start; in a fenced code block
        self.bold = self.italic = self.code = self.head = self.quote = False
        self.prev = ""                         # the character before this one, on this line
        self.on = ""                           # the SGR codes in force now
        self.any, self.newlines = False, 0     # anything written yet; line ends not written yet
        self.col, self.hang = -1, 0            # -1: this line has no indent yet; where a wrapped line goes on
        self.word, self.word_len, self.gap = [], 0, ""

    def feed(self, piece: str) -> None:
        self.buf += clean(piece)
        self._read(False)
        self._send()

    def end(self) -> None:
        self._read(True)
        self._word_out()
        self._style("")
        if self.any:
            self.out.append("\n")
        self.any = False                       # a second end writes nothing
        self._send()

    def _send(self) -> None:
        if self.out:
            self.sink("".join(self.out))
            self.out = []

    def _read(self, final: bool) -> None:
        while self.buf:
            step = self._fence_line if self.fence else self._line_start if self.fresh else self._inline
            if not step(final):
                return

    # -- writing --------------------------------------------------------------------------------------
    def _begin(self) -> None:
        if self.col < 0:
            self.out.append("\n" * self.newlines + "  ")
            self.newlines, self.col, self.any = 0, 0, True

    def _lead(self, text: str, painted: str = "") -> None:
        self._begin()
        self.out.append(painted or text)
        self.col += len(text)

    def _paint(self, text: str, kind: str) -> str:
        return self.look.paint(text, kind) if self.color else text

    def _style(self, codes: str) -> None:
        if self.color and codes != self.on:
            self.out.append(("\x1b[0m" if self.on else "") + (f"\x1b[{codes}m" if codes else ""))
            self.on = codes

    def _codes(self) -> str:
        on = [("bold", self.bold or self.head), ("italic", self.italic), ("accent", self.code), ("dim", self.quote)]
        return ";".join(COLORS[kind] for kind, yes in on if yes)

    def _put(self, text: str) -> None:
        codes = self._codes()
        for part in re.findall(r" +|[^ ]+", text):
            if part[0] == " ":
                self._word_out()
                self.gap += part
            else:
                self.word.append((codes, part))
                self.word_len += len(part)

    def _word_out(self) -> None:
        if not self.word:
            return
        self._begin()
        if self.wrap and self.col > self.hang and self.col + len(self.gap) + self.word_len > self.wrap:
            self.out.append("\n  " + " " * self.hang)
            self.col, self.gap = self.hang, ""
        if self.on != self.word[0][0]:
            self._style("")                    # the gap between two styles has none
        self.out.append(self.gap)
        for codes, part in self.word:
            self._style(codes)
            self.out.append(part)
        self.col += len(self.gap) + self.word_len
        self.word, self.word_len, self.gap = [], 0, ""

    def _line_end(self) -> None:
        self._word_out()
        self._style("")
        self.gap, self.prev, self.col, self.hang = "", "", -1, 0
        self.bold = self.italic = self.code = self.head = self.quote = False
        self.newlines += 1 if self.any else 0

    # -- a line's start ---------------------------------------------------------------------------------
    def _line_start(self, final: bool) -> bool:
        s = self.buf
        rest = s.lstrip(" ")
        pad = s[:len(s) - len(rest)]
        line, nl, _ = rest.partition("\n")
        whole = bool(nl) or final
        if not whole and (not rest or _START_SO_FAR.fullmatch(rest)):
            return False
        if line.startswith("```"):
            if not whole:
                return False
            if "`" not in line[3:]:
                self.buf, self.fence = rest[len(line) + len(nl):], True
                return True
        if _RULE.fullmatch(line):
            self._lead(pad + "-" * 20, pad + self._paint(self.look.g["h"] * 20, "dim"))
            self.buf, self.fresh = rest[len(line):], False
            return True
        if rest[:1] == ">":
            bar = self.look.g["v"] + " "
            self._lead(pad + bar, pad + self._paint(bar, "dim"))
            self.buf, self.hang, self.quote = rest[2 if rest[1:2] == " " else 1:], self.col, True
            return True
        m = _BLOCK.match(line)
        if m and m.group(1):
            self.head, self.buf, self.fresh, self.hang = True, rest[m.end():], False, max(self.col, 0)
            return True
        if m:
            dot = self.look.g["dot"] if self.look.unicode else "-"
            self._lead(pad + (m.group(3) or dot + " "))
            self.buf, self.fresh, self.hang = rest[m.end():], False, self.col
            return True
        self.fresh, self.hang = False, max(self.col, 0) + len(pad)
        return True

    def _fence_line(self, final: bool) -> bool:
        s = self.buf
        if self.fresh:
            rest = s.lstrip(" ")
            line, nl, _ = rest.partition("\n")
            whole = bool(nl) or final
            if not whole and (len(rest) < 3 and not rest.strip("`") or rest.startswith("```")):
                return False
            if line.startswith("```") and not line.strip("` "):
                self.buf, self.fence = rest[len(line) + len(nl):], False
                return True
            if s[0] == "\n":
                self.newlines += 1 if self.any else 0
                self.buf = s[1:]
                return True
            self._lead("  ")
            self.fresh = False
        text, nl, tail = s.partition("\n")
        if text:
            self._begin()
            self._style(COLORS["accent"])
            self.out.append(text)
        if nl:
            self._line_end()
            self.fresh = True
        self.buf = tail
        return True

    # -- inside a line ----------------------------------------------------------------------------------
    def _inline(self, final: bool) -> bool:
        s = self.buf
        c = s[0]
        if c == "\n":
            self._line_end()
            self.buf, self.fresh = s[1:], True
            return True
        m = _PLAIN.match(s)
        if m:
            return self._literal(m.end())
        if c == "\\":
            if len(s) < 2 and not final:
                return False
            if s[1:2] in ("*", "_", "`"):
                self._put(s[1])
                self.buf, self.prev = s[2:], s[1]
                return True
            return self._literal(1)
        if c == "[":
            return self._link(final)
        run = len(s) - len(s.lstrip(c))
        if run == len(s) and not final:
            return False                       # the run may go on, and the next character decides
        if c == "`":
            return self._code(run, final)
        nxt = s[run:run + 1]
        if c == "*":
            return self._stars(run, nxt, final)
        return self._literal(run) if run > 1 else self._italic("_", nxt, final)

    def _literal(self, n: int) -> bool:
        self._put(self.buf[:n])
        self.prev, self.buf = self.buf[n - 1], self.buf[n:]
        return True

    def _stars(self, run: int, nxt: str, final: bool) -> bool:
        if run >= 4:
            return self._literal(run)          # a row of stars, like a hidden PIN
        if run >= 2:
            if self.prev == " " and nxt == " ":
                return self._literal(run)      # 2 ** 3
            if not self.bold and self.prev.isalnum() and nxt.isalnum():
                stop = self.buf.find("\n")
                shut = self.buf.find("**", run, stop if stop >= 0 else len(self.buf))
                if self.prev.isdigit() and nxt.isdigit() or (shut < 0 and (stop >= 0 or final)):
                    return self._literal(run)  # 2**3, x**y
                if shut < 0:
                    return False
            self.bold = not self.bold and nxt not in ("", " ", "\n")
            self.buf, self.prev = self.buf[2:], "*"
            return True
        return self._italic("*", nxt, final)

    def _italic(self, mark: str, nxt: str, final: bool) -> bool:
        if self.prev.isalnum() or self.prev == "_" or nxt in ("", " ", "\n"):
            return self._literal(1)
        at = self._closer(1, mark, final)
        if at == _WAIT:
            return False
        if at == _NONE:
            return self._literal(1)
        inner, tail = self.buf[1:at], self.buf[at + 1:]
        keep, self.italic = self.italic, True
        self._nested(inner)
        self.italic, self.buf, self.prev = keep, tail, mark
        return True

    def _closer(self, i: int, mark: str, final: bool) -> int:
        """Where this ``*`` or ``_`` closes on this line, or _NONE, or _WAIT when more text can still decide."""
        s = self.buf
        stop = s.find("\n")
        whole = stop >= 0 or final
        stop = stop if stop >= 0 else len(s)
        while i < stop:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "`":
                n = len(s[i:stop]) - len(s[i:stop].lstrip("`"))
                shut = next((t.start() for t in re.finditer("`+", s[i + n:stop]) if len(t.group()) == n), None)
                if shut is None and not whole:
                    return _WAIT
                i += n if shut is None else n + shut + n
            elif c == mark:
                n = len(s[i:stop]) - len(s[i:stop].lstrip(c))
                if i + n == stop and not whole:
                    return _WAIT
                after = s[i + n:i + n + 1]
                if n in ((1, 3) if mark == "*" else (1,)) and s[i - 1] != " " and not (after.isalnum() or after == "_"):
                    return i
                i += n
            else:
                i += 1
        return _NONE if whole else _WAIT

    def _code(self, run: int, final: bool) -> bool:
        s = self.buf
        stop = s.find("\n")
        whole = stop >= 0 or final
        stop = stop if stop >= 0 else len(s)
        for t in re.finditer("`+", s[run:stop]):
            at = run + t.start()
            if at + len(t.group()) == stop and not whole:
                return False                   # this close may still grow
            if len(t.group()) == run:
                self.code = True
                self._put(s[run:at])
                self.code = False
                self.buf, self.prev = s[at + run:], "`"
                return True
        return self._literal(run) if whole else False

    def _link(self, final: bool) -> bool:
        s = self.buf
        m = _LINK.match(s)
        if m is None:
            if not final and "\n" not in s and _LINK_SO_FAR.fullmatch(s):
                return False
            return self._literal(1)
        text, url = m.group(1), m.group(2)
        tail = s[m.end():]
        self._nested(text)
        if url != text:
            self._put(" (")
            self.code = True
            self._put(url)
            self.code = False
            self._put(")")
        self.buf, self.prev = tail, ")"
        return True

    def _nested(self, text: str) -> None:
        self.buf, self.prev = text, ""
        while self.buf:
            self._inline(True)


def flat(text: Any, look: Look) -> str:
    """Brain text for a line drawn in one piece (``/turn``): the markers gone, no color, no indent."""
    got: list[str] = []
    md = Markdown(look, got.append, flat=True)
    md.feed(str(text))
    md.end()
    return "\n".join(line[2:] for line in "".join(got).rstrip("\n").split("\n"))


def all_commands() -> list[dict]:
    """Every command as data: the search, ``puffpai help`` and ``puffpai commands --json`` read this."""
    rows = [{"name": name, "args": args, "what": what, "words": COMMAND_WORDS.get(name, "").split(), "here": True}
            for name, args, what in COMMANDS]
    return rows + [{"name": name, "args": "", "what": NOT_YET_WHAT.get(name, ""),
                    "words": COMMAND_WORDS.get(name, "").split(), "here": False} for name in NOT_YET]


def find_commands(query: str) -> tuple[list[str], list[dict]]:
    """``(the words searched for, the commands with a word starting with each of them)``: name, what it does, or
    its search words. A word is found at its start, so /pass finds "password" and never "bypass"."""
    want = re.findall(r"\w+", str(query).lower())
    hits = []
    for row in all_commands():
        have = re.findall(r"\w+", " ".join([row["name"], row["what"], *row["words"]]).lower())
        if want and all(any(h.startswith(w) for h in have) for w in want):
            hits.append(row)
    return want, hits


def tool_line(ev: dict) -> str:
    """A ``progress`` event in plain words: its own line, or what its tool does."""
    line = str(ev.get("line") or "")
    if line and not RAW_TOOL_LINE.fullmatch(line):
        return line
    return TOOL_WORDS.get(str(ev.get("tool") or ""), "Working") + "..."


def tool_failed(tool: Any) -> str:
    words = TOOL_WORDS.get(str(tool or ""))
    return f"That did not work: {words[0].lower()}{words[1:]}." if words else "That did not work."


# -- the Core API, with the terminal key ----------------------------------------------------------------------
class Api:
    """``(status, body)`` calls and the chat stream, to 127.0.0.1 with the terminal key (a program's key,
    D79(c)): the same routes and access a paired device has, no more. Never through a proxy."""

    def __init__(self, info: dict) -> None:
        self.info = info
        self.base = f"http://127.0.0.1:{int(info['port'])}"
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, method: str, path: str, body: dict | None) -> urllib.request.Request:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        return urllib.request.Request(self.base + path, data=data, method=method,
                                      headers={"Authorization": f"Bearer {self.info['token']}",
                                               "Content-Type": "application/json"})

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: float = CALL_TIMEOUT_S) -> tuple[int, Any]:
        try:
            with self._opener.open(self._request(method, path, body), timeout=timeout) as resp:
                status, raw = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        text = raw.decode("utf-8", errors="replace")
        from .cli import owner_words                 # a 401 in the owner's words (D91), as every CLI verb says it
        try:
            return status, owner_words(status, json.loads(text))
        except ValueError:
            return status, owner_words(status, text)

    def stream(self, path: str, body: dict) -> tuple[int, Any, Iterator[dict]]:
        """``(status, error body or None, events)``: NDJSON lines as dicts, as they come."""
        try:
            resp = self._opener.open(self._request("POST", path, body), timeout=STREAM_TIMEOUT_S)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            from .cli import owner_words
            try:
                return exc.code, owner_words(exc.code, json.loads(raw)), iter(())
            except ValueError:
                return exc.code, owner_words(exc.code, raw), iter(())

        def events() -> Iterator[dict]:
            with resp:
                for line in resp:
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        ev = json.loads(text)
                    except ValueError:
                        yield {"type": "error", "error": "The Core sent a line the terminal can't read."}
                        continue
                    if isinstance(ev, dict):
                        yield ev
        return resp.status, None, events()


def said(body: Any, fallback: str = "It did not work.") -> str:
    """The Core's plain reason from a reply body."""
    if isinstance(body, dict):
        for key in ("error", "said", "line"):
            if isinstance(body.get(key), str) and body[key]:
                return body[key]
        probs = body.get("problems")
        if isinstance(probs, list) and probs:
            return " ".join(str(p) for p in probs)
    if isinstance(body, str) and body.strip() and not body.lstrip().startswith("<"):
        return body.strip()[:300]
    return fallback


def short(cid: str) -> str:
    """A card's short id, as shown and as typed: 6 characters of its consent id."""
    cid = str(cid or "")
    return cid[2:8] if cid.startswith("c_") else cid[:6]


def plan_of(rec: dict) -> dict | None:
    """``{build, consent_id}`` when this card is a Toolsmith plan card (its Yes starts a build), else None.
    The Desktop's ``planOf``."""
    covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else None
    if covers is None:
        card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
        covers = card.get("covers") if isinstance(card.get("covers"), dict) else {}
    if not isinstance(covers.get("build"), str) or not covers.get("build") or not covers.get("plan_sha"):
        return None
    return {"build": covers["build"], "consent_id": str(rec.get("consent_id") or rec.get("id") or "")}


def card_ticks(rec: dict) -> dict | None:
    """The tick boxes a card offers (the Desktop's ``cardTicks``): a plan card's offers and model offers, or a
    job's hold card. ``{kind, note, route, items: [{value, label}], body(ticked)}`` or None."""
    card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
    cid = str(rec.get("consent_id") or rec.get("id") or "")
    covers = card.get("covers") if isinstance(card.get("covers"), dict) else {}
    plan = covers.get("card") if isinstance(covers.get("card"), dict) else {}
    offers = [o for o in plan.get("offers") or [] if isinstance(o, dict) and isinstance(o.get("job"), int)]
    models = [m for m in plan.get("model_offers") or []
              if isinstance(m, dict) and isinstance(m.get("value"), str) and m["value"].startswith("model:")]
    downloads = [d for d in plan.get("knowledge_offers") or []
                 if isinstance(d, dict) and isinstance(d.get("value"), str) and d["value"].startswith("knowledge:")]
    if (offers or models or downloads) and isinstance(covers.get("build"), str):
        q = urllib.parse.quote(covers["build"], safe="")
        items = [{"value": o["job"], "label": str(o.get("line") or f"{o.get('job_name') or 'Job ' + str(o['job'])}: "
                                                  f"use {o.get('name') or o.get('package')}")} for o in offers]
        items += [{"value": m["value"], "label": str(m.get("line") or m.get("label") or m["value"])} for m in models]
        items += [{"value": d["value"], "label": str(d.get("line") or d["value"]), "ticked": d.get("ticked") is True}
                  for d in downloads]
        return {"kind": "offers", "route": f"/v1/toolsmith/builds/{q}/use", "items": items,
                "note": "Tick an app to use it for that job instead of searching, or a model to download it "
                        "first (each with its own Yes card). Nothing ticked: it goes as planned."
                        + (" Ticked downloads start after your Yes, one at a time; untick any you do not want."
                           if downloads else ""),
                "body": lambda ticked: {"consent_id": cid, "use": list(ticked)}}
    holds = [h for h in card.get("try_holds") or [] if isinstance(h, dict) and isinstance(h.get("candidate"), str)]
    if holds and isinstance(card.get("build"), str):
        q = urllib.parse.quote(card["build"], safe="")
        return {"kind": "holds", "route": f"/v1/toolsmith/builds/{q}/holds/{urllib.parse.quote(cid, safe='')}",
                "items": [{"value": h["candidate"], "label": str(h.get("name") or h["candidate"])
                           + (f" ({h['url']})" if h.get("url") else "")} for h in holds],
                "note": "Tick the apps Toolsmith may try. With nothing ticked, your Yes lets it try none of them.",
                "body": lambda ticked: {"try": list(ticked)}}
    return None


def _card_build(rec: dict) -> str:
    """The build a card belongs to (a plan card, a job's hold card), or ""."""
    plan = plan_of(rec)
    if plan is not None:
        return plan["build"]
    card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
    return str(card.get("build") or "")


def job_words(status: str, library: object = False) -> str:
    s = str(status or "planned")
    if library and s == "working":
        return JOB_LIBRARY
    if s.startswith("building: "):
        return "trying " + s[len("building: "):]
    return JOB_WORDS.get(s, s.replace("_", " "))


def job_active(status: str) -> bool:
    s = str(status or "")
    return s.startswith("building") or s in ACTIVE_JOBS


# -- the chat -----------------------------------------------------------------------------------------------
class Chat:
    """The REPL and the verbs. Every answer goes through the Core API; nothing is decided here."""

    def __init__(self, api: Api, out: TextIO | None = None, read_line: Callable[[str], str] | None = None,
                 read_hidden: Callable[[str], str] | None = None, open_url: Callable[[str], Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, env: dict | None = None,
                 look: Look | None = None) -> None:
        self.api = api
        self.conversation = ""                     # "": the Desktop's own, as the Core reads a turn with none
        self.look = look or Look(out or sys.stdout, env)
        self.read_line = read_line or input
        self.read_hidden = read_hidden or getpass.getpass
        self.open_url = open_url or webbrowser.open_new_tab
        self.sleep = sleep
        self.shown: set[str] = set()           # the cards this window drew already
        self.watch_every = WATCH_EVERY_S
        self.watch_max_s: float | None = None  # tests: stop watching after this long
        self.doing: str | None = None          # /watch: the step shown with no line end yet, on a live console

    # -- writing ---------------------------------------------------------------------------------------
    def say(self, text: str = "", kind: str = "") -> None:
        """Lines, two spaces in, a long one wrapped to the window. Never an escape code from the text."""
        self._step_end(None)
        for line in clean(text).split("\n"):
            pad = len(line) - len(line.lstrip(" "))
            parts = textwrap.wrap(line, self.look.width - 2, subsequent_indent=" " * pad,
                                  break_on_hyphens=False, break_long_words=False) \
                if line.strip() and self.look.term else [line.rstrip()]   # a pipe or log keeps whole lines
            for part in parts:
                self.look.write("  " + (self.look.paint(part, kind) if kind else part) if part else "")

    def say_md(self, text: str) -> None:
        """The brain's words in one piece: the same drawing as the stream."""
        md = Markdown(self.look)
        md.feed(text)
        md.end()

    def ask(self, prompt: str) -> str:
        self._step_end(None)
        try:
            return self.read_line(self.look.safe(prompt)).strip()
        except (RuntimeError, ValueError):         # input() with no stdin, a closed one or undecodable bytes: the end
            raise EOFError from None

    def marked(self, mark: str, kind: str, text: str) -> None:
        """One line after a mark (a check mark, say), a long one wrapped under its words."""
        lines = textwrap.wrap(clean(text), self.look.width - 3 - len(mark), break_on_hyphens=False,
                              break_long_words=False) if self.look.term else [clean(text)]
        for i, line in enumerate(lines or [""]):
            self.look.write("  " + (self.look.paint(mark, kind) if i == 0 else " " * len(mark)) + " " + line)

    def row(self, label: str, what: str, width: int, tag: str = "") -> None:
        """One command: its name in bold, what it does, then where it lives. A long one wraps under its words."""
        room = max(20, self.look.width - 2 - width - (17 if tag else 0))
        lines = textwrap.wrap(clean(what), room) if self.look.term else [clean(what)]
        for i, line in enumerate(lines or [""]):
            left = self.look.paint(label.ljust(width), "bold") if i == 0 else " " * width
            right = "  " + self.look.paint(tag, "dim") if tag and i == 0 else ""
            self.look.write("  " + left + (line.ljust(room) if right else line) + right)

    def keys(self, question: str, keys: list[tuple[str, str]]) -> str:
        """The keys of a card on one line, then the prompt on its own (the mockup's Terminal view)."""
        shown = "  ".join(f"{self.look.paint('[' + k + ']', 'bold')} {what}" for k, what in keys)
        self.look.write("  " + (question + "  " if question else "") + shown)
        try:
            return self.ask(self.look.paint(">", "accent") + " ").lower()
        except EOFError:                               # no keyboard here (D132): the same as Enter, later
            return ""

    def box(self, title: str, lines: list[str], tag: str = "", ticks: list[str] | None = None) -> None:
        """A card in a box: Unicode lines where they show, else +-| (the old console)."""
        g, w = self.look.g, self.look.width - 6
        title, lines, ticks = clean(title), [clean(x) for x in lines], [clean(x) for x in ticks or []]
        dim = lambda s: self.look.paint(s, "dim")   # noqa: E731 - the box lines only, never the words
        head = textwrap.wrap(title, max(20, w - len(tag) - 2)) or [""]
        body: list[str] = head[1:]
        for ln in lines:
            body += textwrap.wrap(ln, w) or [""]
        for t in ticks or []:
            body += textwrap.wrap(t, w, subsequent_indent="      ") or [""]
        width = min(w, max([len(x) for x in body] + [len(head[0]) + 2 + len(tag), 20]))
        # The title and the card's id sit in the top line, as in the mockup.
        self.look.write("  " + dim(g["tl"]) + " " + head[0] + " " * (width - len(head[0]) - len(tag)) + dim(tag)
                        + " " + dim(g["tr"]))
        for x in body:
            self.look.write("  " + dim(g["v"]) + " " + x.ljust(width) + " " + dim(g["v"]))
        self.look.write("  " + dim(g["bl"] + g["h"] * (width + 2) + g["br"]))

    # -- the start --------------------------------------------------------------------------------------
    def status_line(self) -> str:
        """One line: bot name, brain, vault, Bridge (``Bridge.view()``, D64), builds waiting on her, builds going,
        and Your turn when it holds more than those builds."""
        dot = " " + self.look.paint(self.look.g["dot"], "dim") + " "
        parts: list[str] = []
        st, settings = self.api.call("GET", "/v1/settings")
        name = ((settings or {}).get("bot") or {}).get("name") if isinstance(settings, dict) else None
        parts.append(self.look.paint(clean(name or "BuildBot"), "bold"))
        st, model = self.api.call("GET", "/v1/model")
        if st == 200 and isinstance(model, dict):
            m = clean(((model.get("settings") or {}).get("model")) or "")
            state = clean(((model.get("state") or {}).get("state")) or "")
            parts.append(f"brain {m}" + (f" ({state.replace('_', ' ')})" if m and state and state != "ready" else "")
                         if m else "brain not picked yet")
        else:
            parts.append("brain not checked")
        st, vault = self.api.call("GET", "/v1/vault/status")
        vs = str((vault or {}).get("state") or "") if st == 200 and isinstance(vault, dict) else ""
        parts.append("vault " + (self.look.paint("open", "ok") if vs == "open"
                                 else VAULT_WORDS.get(vs, "not checked")))
        st, bridge = self.api.call("GET", "/v1/bridge")
        bstate = str((bridge or {}).get("state") or "") if st == 200 and isinstance(bridge, dict) else ""
        parts.append("Bridge " + (self.look.paint("OPEN", "warn") if bstate == "open"
                                  else self.look.paint("closed", "ok") if bstate == "closed" else "not checked"))
        cards = self.pending()
        waiting, going = self._builds_now(cards)
        plural = lambda n: f"{n} build{'s' if n != 1 else ''}"   # noqa: E731
        parts.append(self.look.paint(f"{plural(waiting)} waiting on you", "warn") if waiting else "no builds waiting")
        if going:
            parts.append(f"{plural(going)} going")
        n = self._turn_count(cards)
        if n > waiting:
            parts.append(self.look.paint(f"Your turn {n}", "warn"))
        return " " + dot.join(parts)

    def _builds_now(self, cards: list[dict]) -> tuple[int, int]:
        """``(builds waiting on her, builds going)``: waiting on a card of its own, a step or her vault."""
        st, body = self.api.call("GET", "/v1/toolsmith")
        builds = body.get("builds") if st == 200 and isinstance(body, dict) else None
        builds = [b for b in builds or [] if isinstance(b, dict)]
        waiting = {_card_build(c) for c in cards} - {""}
        waiting |= {str(b.get("id")) for b in builds if any(j.get("status") in ("waiting_you", "waiting_vault")
                                                            for j in b.get("jobs") or [])}
        live = {str(b.get("id")) for b in builds if any(job_active(j.get("status")) and j.get("status") != "waiting_yes"
                                                        for j in b.get("jobs") or [])}
        return len(waiting), len(live - waiting)

    def _turn_count(self, cards: list[dict]) -> int:
        st, steps = self.api.call("GET", "/v1/checklist")
        n = len(steps) if st == 200 and isinstance(steps, list) else 0
        return n + len(cards)

    # -- the REPL -------------------------------------------------------------------------------------
    def run(self) -> int:
        self.look.write(self.status_line())
        self.look.write(" " + self.look.paint(TYPE_TO_CHAT, "dim"))
        self.look.write()
        if self.conversation:
            self.recent()
        self.shown.update(c["consent_id"] for c in self.pending())   # the ones waiting now: /turn lists them
        while True:
            try:
                line = self.ask(self.look.paint(">", "accent") + " ")
            except (EOFError, KeyboardInterrupt):
                self.look.write()
                self.say(BYE)
                return 0
            if not line:
                continue
            try:
                if line.startswith("/"):
                    if self.command(line) == "quit":
                        self.say(BYE)
                        return 0
                elif line.lower() in ("exit", "quit", "bye"):
                    self.say(BYE)
                    return 0
                elif DASHBOARD_WORDS.fullmatch(line.strip().lower()):
                    self.cmd_dashboard()
                elif WINDOW_WORDS.fullmatch(line.strip().lower()):
                    self.cmd_window()
                else:
                    self.turn(line)
                self.new_cards()
            except KeyboardInterrupt:
                self.look.write()
                self.say("Stopped. BuildBot keeps going with anything it already started.")
            except GONE_ERRORS:
                self.say(GONE, "bad")
                return 1
            self.look.write()

    def recent(self) -> None:
        """Opened from the window's switch (D115): the last words of this chat, so she sees where she was. The locked
        form, as the window reads them."""
        query = urllib.parse.urlencode({"conversation": self.conversation, "limit": RECENT_LINES})
        status, lines = self.api.call("GET", "/v1/chat/history?" + query)
        if status != 200 or not isinstance(lines, list) or not lines:
            return
        self.say(RECENT_SAID, "dim")
        for m in lines:
            text = str(m.get("content") or "").strip() if isinstance(m, dict) else ""
            if text:
                text = text[:RECENT_CHARS] + "..." if len(text) > RECENT_CHARS else text
                if m.get("role") == "user":
                    self.say("You: " + " ".join(text.split()), "dim")
                else:
                    self.say_md(text)                  # the same drawing as when it was said
        self.look.write()

    def command(self, line: str) -> str | None:
        name, _, rest = line[1:].partition(" ")
        name, rest = name.strip().lower(), rest.strip()
        if not name and not rest:
            return self.cmd_help()                 # "/" alone: the list
        fn = getattr(self, f"cmd_{name}", None) if name in {c[0] for c in COMMANDS} else None
        if fn is None:
            if name in NOT_YET:
                self.say(f"/{name} is not in the terminal yet. Use the Desktop for it.")
                return None
            self.search(name)
            return None
        return fn(rest)

    # -- one chat turn: the Desktop's route, its conversation --------------------------------------------
    def turn(self, text: str) -> bool:
        if holds_a_secret(text):                   # here, so chat --once is covered too; nothing has been sent yet
            self.say(SECRET_NOT_SENT, "warn")
            return False
        before = {c["consent_id"] for c in self.pending()}
        body = {"text": text, "conversation": self.conversation} if self.conversation else {"text": text}
        status, err, events = self.api.stream("/v1/chat/stream", body)
        if status != 200:
            self.say(said(err, "The brain did not answer."), "bad")
            return False
        opens: list[str] = []
        try:
            ok = self._events(events, before, opens)
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()                            # Ctrl+C here: the stream ends; the Core keeps any card open
        for url in opens:
            self.say(f"Open in your browser: {url}")
            if self.ask("  [o] open it now  [Enter] not now  > ").lower() == "o":
                self._open(url)
        return ok

    def _events(self, events: Iterator[dict], before: set[str], opens: list[str]) -> bool:
        """Draw one turn's stream: the words as they come, each tool step in plain words, each Yes card (answered
        here, while the turn waits for it), links to open, and the end."""
        words, ok = "", True
        md: Markdown | None = None
        last_line, failed = "", ""
        try:
            for ev in events:
                kind = ev.get("type")
                if failed and kind != "done":
                    self.say(failed, "warn")
                    failed = ""
                if kind == "token":
                    piece = clean(ev.get("text") or "")
                    if piece:
                        md = md or Markdown(self.look)
                        words += piece
                        md.feed(piece)
                        last_line = ""
                    continue
                if md is not None:
                    md.end()
                    md = None
                if kind == "progress":
                    line = tool_line(ev)
                    if line != last_line:          # the same step twice in a row is one line
                        self.say(line, "dim")
                    last_line = line
                elif kind == "tool_done":
                    if not ev.get("ok"):
                        failed = tool_failed(ev.get("tool"))
                elif kind == "ask":
                    q = ev.get("question") if isinstance(ev.get("question"), dict) else {}
                    cid = str(q.get("consent_id") or q.get("id") or "")
                    rec = self._find_card(cid) if cid else self._new_card(before)
                    if rec is None:
                        self.say("A Yes card was asked for. It waits on Your turn (/turn) and on the Desktop.")
                    else:
                        self.answer(rec, in_chat=True)
                elif kind == "open":
                    opens += [str(u) for u in ev.get("urls") or [] if str(u).startswith(("http://", "https://"))]
                elif kind == "done":
                    final = clean(ev.get("text") or "")
                    # The words between tools come joined by a blank line in the final text: not new words.
                    new = bool(final) and "".join(final.split()) != "".join(words.split())
                    if failed and not new:
                        self.say(failed, "warn")   # with new words, they say why (her No ends a turn this way too)
                    failed = ""
                    if new:                        # more after the streamed words (an opening question)
                        self.say_md(final[len(words):].strip("\n") if words and final.startswith(words) else final)
                    elif not final and not words and ev.get("notice"):
                        self.say(str(ev["notice"]), "dim")
                    changed = [str((c or {}).get("tool") if isinstance(c, dict) else c) for c in ev.get("changed") or []]
                    if changed:
                        self.say("Changed: " + ", ".join(changed))
                elif kind == "error":
                    self.say(str(ev.get("error") or "The brain did not answer."), "bad")
                    ok = False
            if failed:
                self.say(failed, "warn")
        finally:
            if md is not None:
                md.end()                           # the words so far, even when the stream was cut
        return ok

    # -- Yes cards -----------------------------------------------------------------------------------------
    def pending(self) -> list[dict]:
        st, body = self.api.call("GET", "/v1/consent?status=pending")
        return [c for c in body if isinstance(c, dict) and c.get("consent_id")] if st == 200 and isinstance(body, list) \
            else []

    def _find_card(self, cid: str) -> dict | None:
        return next((c for c in self.pending() if c["consent_id"] == cid), None)

    def _new_card(self, before: set[str]) -> dict | None:
        """A card asked for before it had an id (``read_app``'s start card): the first new one to appear."""
        end = time.monotonic() + NEW_CARD_WAIT_S
        while True:
            fresh = [c for c in self.pending() if c["consent_id"] not in before]
            if fresh or time.monotonic() >= end:
                return fresh[0] if fresh else None
            self.sleep(0.2)

    def new_cards(self) -> None:
        """Cards that came while she chatted or watched (a build's hold card, a store app's card): shown inline,
        each once; Enter leaves it for later."""
        for rec in self.pending():
            if rec["consent_id"] in self.shown:
                continue
            self.say("A new Yes card is waiting:", "warn")
            self.answer(rec, later=True)

    def draw(self, rec: dict, ticked: list | None = None) -> dict | None:
        self.shown.add(rec["consent_id"])
        lines = [str(x) for x in rec.get("lines") or []]
        from .consent.shown import PIN_LINE
        readable = any(x and x != PIN_LINE for x in lines)
        if not readable:
            lines.append(NO_DETAIL)
        ticks = card_ticks(rec) if readable else None
        tick_lines = None
        if ticks:
            mark = lambda v: "[x]" if v in (ticked or []) else "[ ]"   # noqa: E731
            tick_lines = [ticks["note"]] + [f"{i:>2} {mark(it['value'])} {it['label']}"
                                            for i, it in enumerate(ticks["items"], 1)]
        self.box(str(rec.get("title") or "Your Yes is needed"), lines + [PRIVACY], f"card {short(rec['consent_id'])}",
                 tick_lines)
        return ticks if readable else {"unreadable": True}

    def answer(self, rec: dict, in_chat: bool = False, later: bool = False, want: str = "") -> str:
        """Draw the card, take her keys, send her answer with this card's ``card_sha``. Returns "yes", "no",
        "later" or "" (not answered). *want*: "no" answers No at once (``puffpai no``)."""
        ticked: list = []
        floor = bool(rec.get("floor"))
        ticks = self.draw(rec)
        unreadable = isinstance(ticks, dict) and ticks.get("unreadable")
        ticks = None if unreadable else ticks
        ticked = [it["value"] for it in (ticks or {}).get("items", []) if it.get("ticked")]   # the card's own (D99)
        if ticked:
            ticks = self.draw(rec, ticked)
        while True:
            if want == "no":
                key = "n"
            elif unreadable:
                key = self.keys("", [("n", "no")] + ([("Enter", "later")] if later else []))
            else:
                key = self.keys("Build this plan?" if plan_of(rec) else "Say Yes to this?",
                                [("p", "yes, with my PIN") if floor else ("y", "yes"), ("n", "no")]
                                + ([("e", "edit ticks")] if ticks else []) + [("?", "what does this mean")]
                                + ([("Enter", "later")] if later else []))
            if key == "" and later:
                self.say(f"Left for later (card {short(rec['consent_id'])}). /yes or /turn shows it again.")
                return "later"
            if key in ("?", "h"):
                self.say(EXPLAIN + (" " + EXPLAIN_FLOOR if floor else "") + (" " + EXPLAIN_TICKS if ticks else ""))
                continue
            if key == "e" and ticks:
                ticked = self._edit_ticks(ticks, ticked)
                continue
            if key in ("n", "no"):
                got = self._send(rec, "no", None, ticks, ticked, in_chat)
                if got == "changed":
                    self.say("That card changed after you saw it, so your No was not taken. /yes shows it again.")
                    return ""
                if got == "retry" and not want:
                    continue
                return "" if got == "retry" else got
            if unreadable:
                self.say(NO_DETAIL)
                continue
            if floor and key in ("y", "yes"):
                self.say(NEEDS_PIN + " Press p to type it.", "warn")
                continue
            if (floor and key in ("p", "pin")) or (not floor and key in ("y", "yes")):
                got = self._yes(rec, floor, ticks, ticked, in_chat)
                if got == "changed":
                    fresh = self._find_card(rec["consent_id"])
                    if fresh is None:
                        self.say(ANSWERED_ELSEWHERE)
                        return ""
                    self.say(CHANGED_AGAIN, "warn")
                    rec, ticks = fresh, self.draw(fresh, ticked)
                    unreadable = isinstance(ticks, dict) and ticks.get("unreadable")
                    ticks = None if unreadable else ticks
                    floor = bool(rec.get("floor"))
                    continue
                if got == "retry":
                    continue
                return got
            self.say("Type y, n" + (", e" if ticks else "") + " or ?." if not floor else "Type p, n or ?.")

    def _edit_ticks(self, ticks: dict, ticked: list) -> list:
        now = list(ticked)
        for i, it in enumerate(ticks["items"], 1):
            self.say(f"{i:>2} {'[x]' if it['value'] in now else '[ ]'} {it['label']}")
        raw = self.ask("  Numbers to tick or untick, like 1 3, then Enter  > ")
        for part in raw.replace(",", " ").split():
            if part.isdigit() and 1 <= int(part) <= len(ticks["items"]):
                v = ticks["items"][int(part) - 1]["value"]
                if v in now:
                    now.remove(v)
                else:
                    now.append(v)
        self.say("Ticked: " + (", ".join(it["label"] for it in ticks["items"] if it["value"] in now) or "nothing"))
        return now

    def _keys_ready(self, build: str, pin_given: str | None) -> tuple[bool, str | None]:
        """D68: when the build needs its cloud key and the vault is locked, open the vault with her PIN first
        (her usual unlock). ``(go on, the PIN typed here or None)``. The Desktop's ``keysReady``."""
        st, b = self.api.call("GET", f"/v1/toolsmith/builds/{urllib.parse.quote(build, safe='')}")
        keys = b.get("keys") if st == 200 and isinstance(b, dict) and isinstance(b.get("keys"), dict) else {}
        if keys.get("unlock") is not True:
            return True, pin_given
        self.say(UNLOCK_FOR_YES)
        pin = pin_given if pin_given else self.read_hidden(self.look.safe("  " + PIN_PROMPT))
        if not pin:
            self.say(UNLOCK_TYPE_PIN)
            return False, None
        ok = self._unlock(pin, quiet=True)
        if not ok:
            return False, None
        return True, pin

    def _yes(self, rec: dict, floor: bool, ticks: dict | None, ticked: list, in_chat: bool) -> str:
        pin = None
        if floor:
            pin = self.read_hidden(self.look.safe("  " + PIN_PROMPT))
            if not pin:
                self.say("No PIN typed, so nothing was answered.")
                return "retry"
        plan = plan_of(rec)
        if plan is not None:
            go, pin = self._keys_ready(plan["build"], pin)
            if not go:
                return "retry"
            if not floor:
                pin = None                     # her PIN opened the vault; a tap card never carries it
        if ticks:
            st, body = self.api.call("PUT", ticks["route"], ticks["body"](ticked))
            if st >= 400:
                self.say("Your ticks were not taken, so nothing was answered: " + said(body), "bad")
                return "retry"
        return self._send(rec, "yes", pin, ticks, ticked, in_chat)

    def _send(self, rec: dict, answer: str, pin: str | None, ticks: dict | None, ticked: list, in_chat: bool) -> str:
        body: dict = {"answer": answer, "factor": "pin" if (answer == "yes" and pin) else "tap",
                      "card_sha": rec.get("card_sha")}
        if answer == "yes" and pin:
            body["pin"] = pin
        cid = rec["consent_id"]
        try:
            st, got = self.api.call("POST", f"/v1/consent/{urllib.parse.quote(cid, safe='')}/answer", body)
        finally:
            body.pop("pin", None)              # the PIN goes once, in this one request
            pin = None
        if st == 409 and isinstance(got, dict) and got.get("code") == "card_changed":
            return "changed"
        if st != 200:
            self.say("Your answer was not taken: " + said(got), "bad")
            return "retry" if st == 403 else ""
        tag = f"card {short(cid)}"
        if answer == "no":
            self.say(f"No recorded ({tag}). Nothing was done.")
            return "no"
        self.say(f"Yes recorded ({tag}).", "ok")
        plan = plan_of(rec)
        if plan is not None:
            if in_chat:
                self.say("Toolsmith starts the build now. /watch follows it here, or keep chatting.")
            else:
                self._start(plan)
        return "yes"

    def _start(self, plan: dict) -> None:
        """After a plan Yes outside a chat turn: ask the Core to start it (safe to ask twice; the Desktop's
        ``afterPlanYes``). Store apps ticked on the plan are added first, each with its own card."""
        st, body = self.api.call("POST", f"/v1/toolsmith/builds/{urllib.parse.quote(plan['build'], safe='')}/start",
                                 {"consent_id": plan["consent_id"]})
        if st != 200:
            self.say("The build did not start: " + said(body), "bad")
            return
        self.say("Building. /watch follows it here, or keep chatting.")

    def _pick(self, arg: str, cards: list[dict]) -> dict | None:
        if not cards:
            self.say("No Yes card is waiting.")
            return None
        if arg:
            hit = [c for c in cards if c["consent_id"] == arg or short(c["consent_id"]).startswith(arg.lower())
                   or c["consent_id"].startswith(arg)]
            if len(hit) == 1:
                return hit[0]
            self.say("No waiting card has that id." if not hit else "More than one card starts like that.")
        elif len(cards) == 1:
            return cards[0]
        self.say("Waiting Yes cards:")
        for c in cards:
            self.say(f"  card {short(c['consent_id'])}  {c.get('title') or 'Your Yes is needed'}")
        self.say("Type /yes <card> or /no <card>.")
        return None

    # -- the commands -----------------------------------------------------------------------------------
    def cmd_help(self, rest: str = "") -> int:
        if rest.strip():
            return self.search(rest)
        for name, args, what in COMMANDS:
            self.row(f"/{name} {args}", what, 26)
        self.say(NOT_YET_LINE, "dim")
        self.say(PIN_NEVER)
        self.say("Plain words are chat with your bot, like on the Desktop. " + PRIVACY, "dim")
        return 0

    def search(self, query: str) -> int:
        """Every command that matches, by name, what it does or its search words. With no match, what she typed
        is never said back: "/unlock1234" would print a PIN. With one, the words said are all in the table."""
        want, hits = find_commands(query)
        if not hits:
            near = difflib.get_close_matches(want[0] if want else "", [c[0] for c in COMMANDS], n=1)
            self.say("There is no such command." + (f" Did you mean /{near[0]}?" if near else "")
                     + " /help lists them. To ask your bot instead, type it in the chat without the /.")
            return 1
        word = " ".join(want)
        self.say(f'{len(hits)} command{" matches" if len(hits) == 1 else "s match"} "{word}":', "dim")
        for row in hits:
            self.row("/" + row["name"], row["what"], 16, "Core" if row["here"] else "use the Desktop")
        return 0

    def cmd_quit(self, rest: str = "") -> str:
        return "quit"

    def cmd_clear(self, rest: str = "") -> None:
        from .wizard.terminal import CLEAR
        if self.look.term and self.look.vt:
            self.look.out.write(CLEAR)
            self.look.out.flush()
            self.look.write(self.status_line())
            self.say("The bot still remembers this conversation.", "dim")
        else:
            self.say("This window can't be cleared from here. The bot still remembers this conversation.")

    def cmd_yes(self, rest: str = "") -> str:
        rec = self._pick(rest, self.pending())
        return self.answer(rec, later=True) if rec else ""

    def cmd_no(self, rest: str = "") -> str:
        rec = self._pick(rest, self.pending())
        return self.answer(rec, want="no") if rec else ""

    def cmd_plan(self, rest: str = "") -> str:
        cards = [c for c in self.pending() if plan_of(c) is not None]
        if rest:
            cards = [c for c in cards if (plan_of(c) or {}).get("build", "").startswith(rest)
                     or short(c["consent_id"]).startswith(rest.lower())]
        if not cards:
            self.say("No plan is waiting for your Yes." + (" /build <goal> makes one." if not rest else ""))
            return ""
        cards.sort(key=lambda c: str(c.get("created_at") or ""), reverse=True)
        return self.answer(cards[0], later=True)

    def cmd_build(self, rest: str = "") -> str:
        goal = rest.strip()
        if not goal:
            self.say("Say what to build, like /build a recipe box for my family.")
            return ""
        self.say("Toolsmith is finding apps for that and planning. This can take a few minutes.", "dim")
        st, b = self.api.call("POST", "/v1/toolsmith/plan", _plan_body(goal), timeout=PLAN_TIMEOUT_S)
        if st != 201 or not isinstance(b, dict):
            self.say("No plan: " + said(b, "Toolsmith could not make a plan this time."), "bad")
            return ""
        cid = str((b.get("consent") or {}).get("id") or "")
        if not cid or b.get("yes"):
            self.say(said(b, "Toolsmith has no plan for this yet."))
            return ""
        rec = self._find_card(cid)
        if rec is None:
            self.say(ANSWERED_ELSEWHERE)
            return ""
        return self.answer(rec, later=True)

    def cmd_watch(self, rest: str = "") -> int:
        build = self._which_build(rest)
        if build is None:
            return 1
        return self.watch(build)

    def _which_build(self, arg: str) -> str | None:
        st, body = self.api.call("GET", "/v1/toolsmith")
        builds = [b for b in (body.get("builds") if st == 200 and isinstance(body, dict) else None) or []
                  if isinstance(b, dict) and b.get("id")]
        builds.sort(key=lambda b: str(b.get("created_at") or ""), reverse=True)
        if arg:
            hit = [b for b in builds if str(b["id"]).startswith(arg)]
            if len(hit) != 1:
                self.say("No build has that id." if not hit else "More than one build starts like that.")
                return None
            return str(hit[0]["id"])
        live = [b for b in builds if any(job_active(j.get("status")) for j in b.get("jobs") or [])]
        if live:
            return str(live[0]["id"])
        if builds:
            return str(builds[0]["id"])
        self.say("There is no build yet. /build <goal> starts one.")
        return None

    def watch(self, build: str) -> int:
        """Follow one build until it ends or needs her. Ctrl+C leaves it running. New cards show inline."""
        path = f"/v1/toolsmith/builds/{urllib.parse.quote(build, safe='')}"
        printed: set[str] = set()
        start = time.monotonic()
        before: set[str] = set()                   # what the step on screen was trying
        try:
            while True:
                st, b = self.api.call("GET", path)
                if st != 200 or not isinstance(b, dict):
                    self.say(said(b, f"There is no build {build}."), "bad")
                    return 1
                if not printed:
                    self.say(f'Build {build} "{b.get("goal") or ""}"', "bold")
                    printed.add("")
                jobs = b.get("jobs") or []
                states = [str(j.get("status") or "") for j in jobs if isinstance(j, dict)]
                for line in _build_lines(b):
                    if line not in printed:
                        printed.add(line)
                        self._step_end(not (before - set(states)) or "working" in states)
                        self._step(line)
                        before = {s for s in states if s.startswith("building")}
                adding = ((b.get("store_adds") or {}).get("state") in ("adding", "starting"))
                active = bool(b.get("planning")) or adding or any(job_active(s) for s in states)
                if not active:
                    self._step_end(bool(states) and all(s == "working" for s in states))
                    self.say("This build has ended.", "bold")
                    for j in jobs:
                        good = j.get("status") == "working" and not j.get("library")
                        self.marked(self.look.check if good else "-", "ok" if good else "warn",
                                    f"{j.get('name')}: {job_words(j.get('status'), j.get('library'))}")
                    return 0
                cards = self.pending()
                for rec in cards:
                    if rec["consent_id"] not in self.shown:
                        self.say("This needs your Yes:", "warn")
                        if self.answer(rec, later=True) == "later" and _card_build(rec) == build:
                            self.say("The build waits for that card. /watch follows it again.")
                            return 0
                going = bool(b.get("planning")) or adding or any(s.startswith("building") for s in states)
                mine = [c for c in self.pending() if _card_build(c) == build]
                if mine and not going:
                    self.say("The build waits for your Yes: " + ", ".join(
                        f"/yes {short(c['consent_id'])}" for c in mine) + ".", "warn")
                    return 0
                if "waiting_you" in states:
                    self.say("This build needs you: a step waits on Your turn. /turn shows it.", "warn")
                    return 0
                if (b.get("keys") or {}).get("unlock") is True or "waiting_vault" in states:
                    self.say(UNLOCK_WAITING, "warn")
                    key = self.ask("  [u] unlock now  [Enter] later  > ").lower()
                    if key not in ("u", ""):
                        self.say(ONLY_KEYS)            # a PIN typed here in view is never used, said back or sent
                    if key != "u" or not self.cmd_unlock():
                        self.say("The build waits for your vault. /unlock, then /watch.")
                        return 0
                if not going and "waiting_yes" in states:
                    self.say("The plan waits for your Yes, on the Desktop or with /plan.")
                    return 0
                if self.watch_max_s is not None and time.monotonic() - start > self.watch_max_s:
                    return 0
                self.sleep(self.watch_every)
        except KeyboardInterrupt:
            self._step_end(None)
            self.look.write()
            self.say(WATCH_STOPPED)
            return 0
        finally:
            self._step_end(None)

    def _step(self, line: str) -> None:
        """A build's step. On a live console it stays open with its mark until the next one, as in the mockup;
        anywhere else (a pipe, the old console) it is just a line."""
        if not (self.look.term and self.look.vt):
            self.say(line)
            return
        text = clean(line)
        self.doing = text
        self.look.write(f"  {self.look.paint(self.look.doing, 'accent')} {text[:self.look.width - 6]}", end="")

    def _step_end(self, ok: bool | None) -> None:
        """Close the step on screen. True: the build went on from it, so a check mark (never on a line that says
        it failed). False: it did not, so a dash. None: it still runs, so its mark stays."""
        text, self.doing = self.doing, None
        if text is None:
            return
        self.look.write("\r\x1b[K", end="")
        if ok is None:
            self.marked(self.look.doing, "accent", text)
        elif ok and not text.endswith(_STEP_BAD) and _LIBRARY_SAID not in text:
            self.marked(self.look.check, "ok", text)
        else:
            self.marked("-", "warn", text)

    def cmd_turn(self, rest: str = "") -> int:
        word, _, n = rest.partition(" ")
        st, steps = self.api.call("GET", "/v1/checklist")
        steps = [s for s in steps if isinstance(s, dict)] if st == 200 and isinstance(steps, list) else []
        if word in ("done", "skip"):
            if not n.strip().isdigit() or not 1 <= int(n) <= len(steps):
                self.say(f"Type /turn {word} <number>, from the list /turn shows.")
                return 1
            step = steps[int(n) - 1]
            if not isinstance(step.get("id"), (str, int)) or step.get("id") == "":
                self.say("That step can't be answered from here. Use Your turn on the Desktop.")
                return 1
            st, body = self.api.call("POST", f"/v1/checklist/{urllib.parse.quote(str(step['id']), safe='')}/{word}")
            if st >= 400:
                self.say(("Not done yet: " if word == "done" else "Not skipped: ") + said(body), "bad")
                return 1
            self.say(("Done: " if word == "done" else "Skipped: ") + flat(step.get("title") or "", self.look))
            return 0
        cards = self.pending()
        if not steps and not cards:
            self.say("Nothing on Your turn.")
            return 0
        for i, s in enumerate(steps, 1):
            self.say(f"{i}  {flat(s.get('title') or '', self.look)}", "bold")
            if s.get("why"):
                self.say(f"   {flat(s['why'], self.look)}")     # both can be the brain's words (checklist_add)
            if s.get("kind") == "run_command" and s.get("text"):
                self.say(f"   Run in {s.get('shell') or 'powershell'}:")
                self.say(f"     {s['text']}")
            if s.get("kind") in ("open_url",) and (s.get("get_it") or s.get("text")):
                self.say(f"   Open: {s.get('get_it') or s.get('text')}")
            if s.get("kind") == "enter_secret" or (s.get("admin") and s.get("kind") == "run_command"):
                self.say("   The key box and Run as administrator are on the Desktop's Your turn.", "dim")
            if s.get("check_failed"):
                self.say(f"   Not done yet: {s['check_failed']}", "warn")
        if steps:
            self.say("/turn done <n> when you did it, /turn skip <n> to skip it.", "dim")
        for c in cards:
            self.say(f"Yes card {short(c['consent_id'])}: {c.get('title') or 'Your Yes is needed'}  (/yes {short(c['consent_id'])})")
        return 0

    def cmd_apps(self, rest: str = "") -> int:
        st, rows = self.api.call("GET", "/v1/registry")
        rows = [r for r in rows if isinstance(r, dict) and r.get("status") != "removed"] \
            if st == 200 and isinstance(rows, list) else []
        if not rows:
            self.say("Nothing installed yet.")
            return 0
        for r in rows:
            status = str(r.get("status") or "")
            if status == "stopped" and r.get("process") is False:
                status = "ready"
            h = r.get("health") if isinstance(r.get("health"), dict) else {}
            health = "working" if h.get("ok") else ("not checked yet" if not h.get("checked_at")
                                                    else f"not working: {h.get('reason') or 'no reason given'}")
            out = " (outside app)" if str(r.get("origin") or "").startswith("adopted:") else ""
            self.say(f"{r.get('name') or r.get('id')} ({r.get('id')}){out}: {status.replace('_', ' ')}, {health}")
        self.say("/open <app> opens one in your browser.", "dim")
        return 0

    def cmd_open(self, rest: str = "") -> int:
        want = rest.strip().lower()
        if not want:
            self.say("Say which app, like /open freshtable. /apps lists them.")
            return 1
        st, rows = self.api.call("GET", "/v1/registry")
        rows = [r for r in rows if isinstance(r, dict) and r.get("status") != "removed"] \
            if st == 200 and isinstance(rows, list) else []
        hit = [r for r in rows if str(r.get("id")).lower() == want or str(r.get("name") or "").lower() == want] or \
              [r for r in rows if str(r.get("id")).lower().startswith(want) or str(r.get("name") or "").lower().startswith(want)]
        if len(hit) != 1:
            self.say("There is no app by that name. /apps lists them." if not hit else "More than one app matches: "
                     + ", ".join(str(r.get("name") or r.get("id")) for r in hit))
            return 1
        row = hit[0]
        ui = row.get("ui") or {}
        pages = [p for p in ui.get("pages") or [] if isinstance(p, dict) and p.get("id")]
        page = pages[0]["id"] if pages else ("program" if ui.get("open") else None)
        name = str(row.get("name") or row.get("id"))
        if page is None:
            self.say(f"{name} has no page or window to open. Ask about it in chat instead.")
            return 1
        st, got = self.api.call("POST", f"/v1/packages/{urllib.parse.quote(str(row['id']), safe='')}/open/"
                                        f"{urllib.parse.quote(str(page), safe='')}")
        if st != 200 or not isinstance(got, dict):
            self.say(f"{name} did not open: " + said(got), "bad")
            return 1
        if got.get("open") == "program":
            self.say(f"{name} opens in its own window.")
            return 0
        url = str(got.get("url") or "")
        if not url.startswith(("http://", "https://")):
            self.say(f"{name} did not open: the Core gave no web address for it.", "bad")
            return 1
        return 0 if self._open(url, name) else 1

    def _open(self, url: str, name: str = "") -> bool:
        """Her real browser, a new tab (D13, D83). Only a plain http or https address (no spaces or control
        codes), so nothing but a web page is ever opened from here."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc or any(ord(c) <= 0x20 or c in '"<>\\^`{|}'
                                                                            or 0x7F <= ord(c) <= 0x9F for c in url):
            self.say("That address can't be opened from here.", "bad")
            return False
        try:
            self.open_url(url)
        except Exception:  # noqa: BLE001 - no browser: the address is said, she can open it herself
            self.say(f"Your browser did not open. The address: {url}")
            return False
        self.say(f"Opened {name + ' ' if name else ''}in a new tab of your browser: {url}")
        return True

    def cmd_dashboard(self, rest: str = "") -> int:
        """D94: the Desktop in her browser, signed in with a one-time pair link when it is PuffPAI's own (D108)."""
        from . import desktop
        from .paths import state_dir
        link = desktop.dashboard_link(state_dir(), int(self.api.info["port"]))
        opened = self._open(link.url, "your dashboard")
        self.say(PAIR_ONCE if link.code else link.line, "dim")
        return 0 if opened else 1

    def cmd_window(self, rest: str = "") -> int:
        """D81: the PuffPAI window, on this Core."""
        try:
            from .window import open_window
        except ImportError:
            self.say(NO_WINDOW)
            return 1
        opened, line = open_window(int(self.api.info["port"]))
        self.say(line)
        return 0 if opened else 1

    def cmd_bridge(self, rest: str = "") -> int:
        action = rest.strip().lower()
        if action == "close":
            st, view = self.api.call("POST", "/v1/bridge/close", {})
            self.say("The Bridge is closed." if st == 200 else "The Bridge did not close: " + said(view))
            return 0 if st == 200 else 1
        if action == "open":
            st, view = self.api.call("POST", "/v1/bridge/open", {})
            if st != 200 and isinstance(view, dict) and view.get("needs_pin"):
                for r in (view.get("safe_mode") or {}).get("reasons") or []:
                    self.say(f"Safe mode: {r}", "warn")
                self.say("Opening the Bridge now needs your PIN. Leave it empty to keep it closed.")
                pin = self.read_hidden(self.look.safe("  " + PIN_PROMPT))
                if not pin:
                    self.say("The Bridge stays closed.")
                    return 1
                body = {"pin": pin}
                pin = None
                try:
                    st, view = self.api.call("POST", "/v1/bridge/open", body)
                finally:
                    body.clear()
            if st != 200:
                self.say("The Bridge stays closed: " + said(view), "bad")
                return 1
        elif action:
            self.say("Type /bridge, /bridge open or /bridge close.")
            return 1
        else:
            st, view = self.api.call("GET", "/v1/bridge")
            if st != 200:
                self.say("The Bridge did not answer: " + said(view), "bad")
                return 1
        lines = view.get("said") if isinstance(view, dict) and isinstance(view.get("said"), list) else []
        state = str((view or {}).get("state") or "") if isinstance(view, dict) else ""
        if not lines:
            lines = ["The Bridge is open." if state == "open" else "The Bridge is closed."]
        # Bridge.view()'s own words (D64), which say open or closed; open is in color too, never color alone.
        for i, line in enumerate(lines):
            self.say(str(line), "warn" if state == "open" and i == 0 else "")
        return 0

    def cmd_vault(self, rest: str = "") -> int:
        word, _, extra = rest.strip().partition(" ")
        word = word.lower()
        if word == "unlock":
            return 0 if self.cmd_unlock(extra) else 1     # a PIN typed after it is refused, never sent
        if word == "recover":
            return self._vault_recover(extra)      # asks for each hidden, whatever came after it
        if extra.strip() or word not in ("", "lock", "status"):
            self.say(VAULT_ARG)                    # a flag or a word it doesn't know: it does not run, and never says it
            return 1
        if word == "lock":
            return self.cmd_lock()
        st, v = self.api.call("GET", "/v1/vault/status")
        if st != 200 or not isinstance(v, dict):
            self.say("The vault did not answer: " + said(v), "bad")
            return 1
        state = str(v.get("state") or "")
        self.say("Your vault is " + (self.look.paint("open", "ok") if state == "open" else VAULT_WORDS.get(state, state))
                 + ".")
        if isinstance(v.get("items"), int):
            self.say(f"It holds {v['items']} item{'s' if v['items'] != 1 else ''}.")
        if v.get("idle_lock_minutes"):
            self.say(f"It locks itself after {v['idle_lock_minutes']} minutes with no use.")
        self.say(PIN_NEVER)
        if state == "not_set_up":
            self.say("Setup makes it, with your PIN: puffpai setup, or Vault on the Desktop.", "dim")
        else:
            self.say("/unlock opens it, /lock locks it." if state != "open" else "/lock locks it now.", "dim")
            self.say("Forgot your PIN? /vault recover, with your recovery code.", "dim")
        return 0

    def _vault_recover(self, extra: str = "") -> int:
        """``/vault recover``, v1's ``_vault_recover`` on ``POST /v1/vault/recover``: the recovery code and the new PIN
        twice, each read hidden. No flag or word carries one: *extra* is never used or said back. A mismatch sends
        nothing. The route holds the throttle and checks the PIN again; the check here spares her a wasted try."""
        if extra.strip():
            self.say(RECOVER_ARG, "warn")
        st, v = self.api.call("GET", "/v1/vault/status")
        if st != 200 or not isinstance(v, dict):
            self.say("The vault did not answer: " + said(v), "bad")
            return 1
        if v.get("state") == "not_set_up":
            self.say(RECOVER_NO_VAULT)
            return 1
        waiting = v.get("inbox_waiting") if isinstance(v.get("inbox_waiting"), int) else 0
        for line in RECOVER_INTRO:
            self.say(line)
        code = self._secret("  " + RECOVER_CODE_PROMPT).strip()
        if not code:
            self.say(RECOVER_NO_CODE)
            return 1
        pin = self._secret("  " + RECOVER_PIN_PROMPT)
        from .zone1.pin_rules import validate_vault_secret
        ok, why = validate_vault_secret(pin)
        if not ok:
            self.say(why, "warn")                  # the rule's own words, which never repeat the PIN
            self.say(RECOVER_STILL_WORKS)
            return 1
        if self._secret("  " + RECOVER_AGAIN_PROMPT) != pin:
            self.say(RECOVER_MISMATCH, "warn")
            return 1
        body = {"code": code, "new_pin": pin}
        code = pin = ""
        try:
            st, got = self.api.call("POST", "/v1/vault/recover", body)
        except GONE_ERRORS:                        # the re-key may have finished before the reply was cut
            self.say(GONE, "bad")
            self.say(RECOVER_MAYBE)
            return 1
        finally:
            body.clear()                           # they go once, in this one request
        if st >= 500:
            self.say(said(got, "The recovery did not finish."), "bad")
            self.say(RECOVER_MAYBE)
            return 1
        if st != 200 or not isinstance(got, dict):
            self.say(said(got, "The recovery was not taken."), "bad")
            self.say(RECOVER_STILL_WORKS if isinstance(got, dict) and got.get("code") == "weak"
                     else "Nothing was changed.")
            return 1
        return self._new_code(got, waiting)

    def _new_code(self, got: dict, waiting: int = 0) -> int:
        """The new recovery code: said once, as v1's ceremony does it. At a terminal she types it back to check it; a
        match takes it off the screen where the terminal lets us. Enter skips, and the code stands. No terminal: said,
        not checked. v1's replacing a code after three wrong tries needs the PIN, which is gone by now, so here she
        is told how to make a new one."""
        for line in RECOVER_DONE:
            self.say(line, "ok")
        new, got["recovery_code"] = str(got.get("recovery_code") or ""), ""
        if waiting:                                # v1 says what the recovery filed; the reply is not lost if this fails
            try:
                st, v = self.api.call("GET", "/v1/vault/status")
            except GONE_ERRORS:
                st, v = 0, None
            left = v.get("inbox_waiting") if st == 200 and isinstance(v, dict) else None
            if isinstance(left, int) and left < waiting:
                n = waiting - left
                self.say(f"{n} item{'s' if n != 1 else ''} kept while your vault was locked went into it.")
        if not new:
            self.say(RECOVER_NO_NEW_CODE, "warn")
            return 1
        from .wizard.lines import CLEARED, NOT_CLEARED, SHOWN_ONCE_TERMINAL
        self.say("Your NEW recovery code:")
        self.say("  " + new, "bold")
        self.say(str(got.get("note") or ""))
        self.say(SHOWN_ONCE_TERMINAL, "warn")
        if self.read_hidden is getpass.getpass and not _stdin_is_tty():
            self.say(RECOVER_NOT_CHECKED, "dim")
            return 0
        for left in range(RECOVER_BACK_TRIES - 1, -1, -1):
            typed = self._secret("  " + RECOVER_BACK_PROMPT).strip()
            if not typed:
                self.say(RECOVER_NOT_CHECKED, "dim")
                return 0
            body = {"code": typed}
            typed = ""
            try:
                st, seen = self.api.call("POST", "/v1/vault/recovery-code/test", body)
            finally:
                body.clear()
            if st == 200 and isinstance(seen, dict) and seen.get("ok"):
                new = ""
                if self.look.term and self.look.vt:
                    from .wizard.terminal import CLEAR
                    self.look.out.write(CLEAR)
                    self.look.out.flush()
                    self.say(CLEARED["terminal"], "ok")
                else:
                    self.say(NOT_CLEARED)
                return 0
            if st != 200:
                self.say(said(seen, "It could not be checked."), "warn")
                self.say(RECOVER_NOT_CHECKED, "dim")
                return 0
            self.say(RECOVER_BACK_WRONG if left else RECOVER_BACK_LAST, "warn")
        return 0

    def _secret(self, prompt: str) -> str:
        """One secret, never shown and never through the chat's input. At a terminal, or with a stand-in: hidden. From a
        pipe: one line, as v1's ``_read_pin`` reads it, since getpass on Windows reads the console itself and would wait
        for a keyboard nobody is at. The end of the input, no input at all, a closed one or a bad byte is nothing typed."""
        try:
            if self.read_hidden is not getpass.getpass or _stdin_is_tty():
                return self.read_hidden(self.look.safe(prompt))
            self.look.write(prompt)
            return sys.stdin.readline().rstrip("\r\n") if sys.stdin is not None else ""
        except (EOFError, OSError, ValueError):
            return ""

    def cmd_unlock(self, rest: str = "") -> bool:
        if rest.strip():
            self.say(PIN_ARG, "warn")              # what she typed is not sent anywhere
            return False
        pin = self._secret("  " + PIN_PROMPT)
        if not pin:
            self.say("No PIN typed. Your vault stays as it is.")
            return False
        return self._unlock(pin)

    def _unlock(self, pin: str, quiet: bool = False) -> bool:
        body = {"method": "pin", "secret": pin}
        pin = ""
        try:
            st, got = self.api.call("POST", "/v1/vault/unlock", body)
        finally:
            body.clear()                           # the PIN goes once, in this one request
        if st != 200:
            self.say("Your vault did not open" + (", so nothing was answered" if quiet else "") + ": "
                     + said(got, "the PIN was not taken."), "bad")
            return False
        st, v = self.api.call("GET", "/v1/vault/status")
        idle = v.get("idle_lock_minutes") if st == 200 and isinstance(v, dict) else None
        self.say("Your vault is open." + (f" It locks again after {idle} minutes with no use, or with /lock."
                                          if idle else " /lock locks it."), "ok")
        return True

    def cmd_lock(self, rest: str = "") -> int:
        st, got = self.api.call("POST", "/v1/vault/lock", {})
        self.say("Your vault is locked." if st == 200 else "Your vault did not lock: " + said(got))
        return 0 if st == 200 else 1

    def once(self, text: str) -> bool:
        """``puffpai chat --once``: one message, its answer, then stop. A card asks for a key this has none of."""
        try:
            return self.turn(text)
        except EOFError:
            self.say(ONCE_CARD, "warn")
            return False

    def _got(self, path: str) -> Any:
        """A route's answer, or None when it says no."""
        st, got = self.api.call("GET", path)
        return got if st == 200 else None

    def cmd_agents(self, rest: str = "") -> int:
        rows = self._got("/v1/registry")
        rows = [r for r in rows if isinstance(r, dict) and r.get("status") != "removed"] if isinstance(rows, list) else []
        running = sum(1 for r in rows if r.get("status") == "running")
        broken = [str(r.get("name") or r.get("id")) for r in rows if r.get("status") == "running" and not (r.get("health") or {}).get("ok")]
        vault, model, steps, tool = (self._got(p) for p in ("/v1/vault/status", "/v1/model", "/v1/checklist", "/v1/toolsmith"))
        brain = str(((model or {}).get("settings") or {}).get("model") or "") if isinstance(model, dict) else ""
        builds = (tool or {}).get("builds") if isinstance(tool, dict) else None
        now = {"archivist": f"Your vault is {str(vault.get('state') or 'not checked').replace('_', ' ')}." if isinstance(vault, dict) else "",
               "concierge": f"The brain: {brain}." if brain else "",
               "steward": f"{running} running, {len(rows) - running} not running.",
               "caretaker": "Not working: " + ", ".join(broken) + "." if broken else "Every running app answers.",
               "majordomo": (f"{len(steps)} things on Your turn." if steps else "Nothing on Your turn.") if isinstance(steps, list) else "",
               "toolsmith": f"{len(builds)} builds so far." if isinstance(builds, list) else ""}
        for key, name, does in AGENTS:
            self.say(f"{name}: {does}")
            if now.get(key):
                self.say("  " + now[key], "dim")
        self.say("Only Toolsmith may use a cloud model, and only if you pick one.", "dim")
        return 0

    def _ledger_line(self, rec: dict, names: dict) -> str:
        if rec.get("event") == "chain_restarted":
            return "The outside log started again."
        if rec.get("event") == "chain_rotated":
            return "The outside log started a new file."
        subject, host = str(rec.get("subject") or ""), str(rec.get("host") or "a host it did not name")
        who, why = names.get(subject) or subject or "Something", LEDGER_WHY.get(str(rec.get("code") or ""), "")
        tail = f": {why}." if why else "."
        what = {"done": f"reached {host}", "failed": f"was allowed to reach {host}, but it did not work"}.get(str(rec.get("outcome")), f"was refused {host}")
        return f"{str(rec.get('t') or '').replace('T', ' ')[:19]}  {who} {what}{tail}"

    def cmd_outside(self, rest: str = "") -> int:
        """v1's name for it: what leaves your computer. ``log [n]``: only the log, the last *n* lines."""
        words = rest.split()
        if not words or words[0] != "log":
            self.cmd_bridge()
        count = int(words[1]) if len(words) > 1 and words[1].isdigit() else OUTSIDE_LINES
        st, lines = self.api.call("GET", f"/v1/bridge/ledger?limit={max(1, min(count, 2000))}")
        if st != 200 or not isinstance(lines, list):
            self.say("The outside log did not answer: " + said(lines), "bad")
            return 1
        if not lines:
            self.say("Nothing has tried to leave your computer yet.")
            return 0
        rows = self._got("/v1/registry")
        names = {str(r.get("id")): str(r.get("name") or r.get("id")) for r in rows if isinstance(r, dict)} if isinstance(rows, list) else {}
        for rec in lines:
            if isinstance(rec, dict):
                self.say(self._ledger_line(rec, names))
        return 0

    def cmd_keys(self, rest: str = "") -> int:
        """Which keys are saved, by name. A value is never asked for here but by ``add``, typed hidden, and never shown."""
        words = rest.split()
        if len(words) == 2 and words[0] in ("add", "remove"):
            path = "/v1/toolsmith/keys/" + urllib.parse.quote(words[1], safe="")
            if words[0] == "remove":
                st, got = self.api.call("DELETE", path)
                self.say(said(got, "The key is removed.") if st != 200 else str((got or {}).get("line") or "The key is removed."))
                return 0 if st == 200 else 1
            value = self._secret("  Paste the key (it does not show): ").strip()
            if not value:
                self.say("Nothing was saved.")
                return 1
            body = {"value": value}
            value = None
            st, got = self.api.call("PUT", path, body)
            body = None
            if st not in KEY_KEPT:
                self.say("The key was not saved: " + said(got))
                return 1
            self.say(str((got.get("line") if isinstance(got, dict) else "") or KEY_KEPT[st]))   # saved, or sealed for later
            return 0
        if words:
            self.say("Say keys, keys add <company> or keys remove <company>.")
            return 1
        providers = self._got("/v1/toolsmith/providers")
        names = {str(p.get("id")): str(p.get("company") or p.get("id")) for p in providers if isinstance(p, dict)} if isinstance(providers, list) else {}
        kept = self._got("/v1/toolsmith/keys")
        words_for = {True: "saved", False: "not saved", None: "can't tell while your vault is locked"}
        for k in kept if isinstance(kept, list) else []:
            if isinstance(k, dict):
                pid = str(k.get("provider") or "")
                self.say(f"{names.get(pid, pid)} ({pid}), for Toolsmith: {words_for.get(k.get('kept'), 'not known')}")
        hub = self._got("/v1/github/key")
        if isinstance(hub, dict):
            self.say("GitHub: " + str(hub.get("line") or words_for.get(hub.get("linked"), "not known")))
        apis = self._got("/v1/toolsmith/apis")
        for a in (apis or {}).get("apis") or [] if isinstance(apis, dict) else []:
            if isinstance(a, dict):
                self.say(f"{a.get('name') or a.get('id')}, an API you added: {words_for.get(a.get('key_linked', a.get('linked')), 'no key needed or not known')}")
        steps = self._got("/v1/checklist")
        for s in steps if isinstance(steps, list) else []:
            if isinstance(s, dict) and s.get("kind") == "enter_secret":
                self.say(f"An app asks for a key: {s.get('title') or s.get('id')}. It is on Your turn.")
        self.say("A value is never shown. Keys are kept in your vault.", "dim")
        return 0

    def cmd_prove(self, rest: str = "") -> int:
        """Ask the running bot to prove itself: its parts, then one real chat turn. Nothing is judged from this shell."""
        bad = 0

        def check(ok: bool, text: str) -> None:
            nonlocal bad
            bad += 0 if ok else 1
            self.marked(self.look.check if ok else "x", "ok" if ok else "bad", text)
        health = self._got("/v1/health")
        check(isinstance(health, dict) and health.get("ok") is True, f"The Core answers, version {(health or {}).get('version', 'not known')}.")
        model = self._got("/v1/model")
        state = (model or {}).get("state") or {} if isinstance(model, dict) else {}
        name = str(((model or {}).get("settings") or {}).get("model") or "") if isinstance(model, dict) else ""
        check(state.get("state") in ("ready", "answering", "loaded"), f"The brain {name or '(none picked)'}: {str(state.get('state') or 'not known').replace('_', ' ')}."
              + (f" {state['reason']}" if state.get("reason") else ""))
        vault = self._got("/v1/vault/status")
        check(isinstance(vault, dict) and vault.get("state") in ("open", "locked"), f"The vault: {str((vault or {}).get('state') or 'not known').replace('_', ' ')}.")
        bridge = self._got("/v1/bridge")
        check(isinstance(bridge, dict), f"The Bridge: {(bridge or {}).get('state', 'not known')}.")
        rows = self._got("/v1/registry")
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("status") == "running":
                h = r.get("health") or {}
                check(bool(h.get("ok")), f"{r.get('name') or r.get('id')}: " + ("working." if h.get("ok") else f"not working: {h.get('reason') or 'no reason given'}."))
        st, got = self.api.call("POST", "/v1/chat", {"text": PROVE_ASK, "conversation": "prove"}, timeout=PROVE_TIMEOUT_S)
        words = str(got.get("text") or "").strip() if st == 200 and isinstance(got, dict) else ""
        check(bool(words), "One real chat turn: it answered." if words else "One real chat turn: " + said(got, "no answer."))
        self.say("It works." if not bad else f"{bad} of these did not pass.", "ok" if not bad else "warn")
        return 0 if not bad else 1

    def cmd_brain(self, rest: str = "") -> int:
        if rest.strip():
            st, got = self.api.call("PUT", "/v1/model", {"model": rest.strip()})
            self.say(str(got.get("line")) if st == 200 and isinstance(got, dict) else "The brain did not change: " + said(got), "" if st == 200 else "bad")
            return 0 if st == 200 else 1
        st, m = self.api.call("GET", "/v1/model")
        if st != 200 or not isinstance(m, dict):
            self.say("The brain did not answer: " + said(m), "bad")
            return 1
        model = str((m.get("settings") or {}).get("model") or "")
        state = m.get("state") or {}
        if not model:
            self.say("No brain is picked yet. Setup picks one (puffpai setup).")
            return 0
        words = str(state.get("state") or "not checked").replace("_", " ")
        self.say(f"The brain is {model}, on this computer: {words}"
                 + (f" ({state['reason']})" if state.get("reason") else "") + ".")
        self.say("/brain <model> changes it to one on this computer.", "dim")
        return 0


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _plan_body(goal: str) -> dict:
    """``POST /v1/toolsmith/plan``'s body for her words, as the Desktop's Add box makes it (``addSource``): a
    GitHub address is her pasted project; any other words are the goal."""
    from .wiring import _GITHUB_IN
    m = _GITHUB_IN.search(goal)
    if m:
        repo = m.group(2).rstrip(".,;:!?)")
        repo = repo[:-4] if repo.endswith(".git") else repo
        url = f"https://github.com/{m.group(1)}/{repo}"
        return {"goal": f"Add {repo} from GitHub",
                "jobs": [{"name": repo, "does": f"Run {repo}, the project at {url}", "pasted": [url]}]}
    return {"goal": goal[:600]}


def _build_lines(b: dict) -> list[str]:
    """What a build is doing, in plain lines, from ``GET /v1/toolsmith/builds/<id>``."""
    out: list[str] = []
    if b.get("planning"):
        out.append("Toolsmith is planning.")
    rec = b.get("store_adds") if isinstance(b.get("store_adds"), dict) else None
    if rec:
        out += [str(a.get("line")) for a in rec.get("apps") or [] if isinstance(a, dict) and a.get("line")]
        if rec.get("line"):
            out.append(str(rec["line"]))
    for j in b.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        run = j.get("run") if isinstance(j.get("run"), dict) else {}
        out.append(f"{j.get('name')}: {job_words(j.get('status'), j.get('library'))}" + (f" ({run['now']})" if run.get("now") else ""))
        if run.get("line"):
            out.append(f"{j.get('name')}: {run['line']}")
    if b.get("said"):
        out.append(str(b["said"]))
    return out


# -- the entry points ------------------------------------------------------------------------------------------
def not_running_lines() -> list[str]:
    from .wizard.terminal import cli_command, how_to_run, launcher
    start = launcher()
    return [NOT_RUNNING, HOW_TO_START_CMD.format(how=how_to_run(start)) if start.is_file() else HOW_TO_START.format(serve=cli_command('serve'))]


def _console_out() -> TextIO:
    """UTF-8 where possible. On Windows, an output that is this console but that Python could not open as one
    (``> con`` in Chat with BuildBot.cmd is write-only, so Python falls back to the old code page) is replaced by
    the console itself, ``CONOUT$``, which Python writes as Unicode. Anything else: stdout as it is."""
    out = sys.stdout
    try:
        plain = (out.encoding or "").lower().replace("-", "").replace("_", "") not in ("utf8",)
        if os.name == "nt" and plain and out.isatty():
            return open("CONOUT$", "w", encoding="utf-8", errors="replace")    # noqa: SIM115 - lives as long as the chat
    except (AttributeError, OSError, ValueError):
        pass
    return out


def connect(out: TextIO | None = None, **kw: Any) -> Chat | None:
    """A Chat on the running Core, or None (after saying so plainly) when none runs."""
    from .cli import _terminal
    try:
        info = _terminal()
    except GONE_ERRORS:
        info = None
    if info is None:
        from .cli import held_now
        held = held_now()                          # D91: this bot runs but is not reachable with this key
        if held:
            (out or sys.stdout).write(held + "\n")
            (out or sys.stdout).flush()
            return None
        for line in not_running_lines():
            (out or sys.stdout).write(line + "\n")
        (out or sys.stdout).flush()
        return None
    api = Api(info)
    try:
        status, _ = api.call("GET", "/v1/consent?status=pending")    # the key, once: a refused one says so (D91)
    except GONE_ERRORS:
        status = 0
    if status == 401:
        from .cli import KEY_REFUSED
        (out or sys.stdout).write(KEY_REFUSED + "\n")
        (out or sys.stdout).flush()
        return None
    return Chat(api, out=out, **kw)


def run_verb(fn_name: str, arg: str = "", out: TextIO | None = None, **kw: Any) -> int:
    """One shell verb (``puffpai build``, ``yes``, ``watch``...) on the running Core."""
    chat = connect(out, **kw)
    if chat is None:
        return 1
    try:
        got = getattr(chat, fn_name)(arg)
    except KeyboardInterrupt:
        chat.look.write()
        return 130
    except GONE_ERRORS:
        chat.say(GONE, "bad")
        return 1
    if isinstance(got, bool):
        return 0 if got else 1
    if isinstance(got, int):
        return got
    return 0 if got in ("yes", "no", "later", None) else 1


def help_verb(query: str = "", out: TextIO | None = None) -> int:
    """``puffpai help [word]``: the /help list or search, with no Core needed."""
    return Chat(None, out=out or sys.stdout).cmd_help(query)


def commands_verb(as_json: bool = False, out: TextIO | None = None) -> int:
    """``puffpai commands [--json]``: every command, for scripts and other AIs."""
    if as_json:
        (out or sys.stdout).write(json.dumps(all_commands(), indent=2) + "\n")
        return 0
    return help_verb("", out)


def main(out: TextIO | None = None, **kw: Any) -> int:
    """``puffpai`` with no words: the chat."""
    chat = connect(out if out is not None else _console_out(), **kw)
    if chat is None:
        return 1
    asked = os.environ.get(CONVERSATION_ENV, "")
    if asked:
        from .zone2.conversations import valid_id
        chat.conversation = asked if valid_id(asked) else ""
    try:
        return chat.run()
    except GONE_ERRORS:
        chat.say(GONE, "bad")
        return 1

