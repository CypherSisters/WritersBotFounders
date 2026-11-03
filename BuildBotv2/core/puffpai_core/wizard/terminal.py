"""The terminal renderer: ``puffpai setup`` (03 1.6).

It draws the engine's screens and nothing else: no wording or rules of its
own. Secrets (the PIN, keys, the recovery code typed back) are read with no
echo. After the recovery code is typed back and matches, it clears the
screen and the scrollback with ``ESC[3J ESC[2J ESC[H``, but only when the
output is a terminal and (on Windows) the console agreed to read those codes
(``ENABLE_VIRTUAL_TERMINAL_PROCESSING``); otherwise it writes nothing and
says it could not clear the window. Inside tmux it also runs
``tmux clear-history`` for its own pane. It never says the screen was
cleared, and it shows the honest line that the terminal or SSH app may keep
a copy (D36(c)).

A ``working`` screen (the build step) is drawn, then asked for again after its
``poll_s``; the engine sends only new lines each time.

``puffpai setup`` (:func:`main`) runs on a Core: if one is already running for
this bot it uses that one through the Core API (``RemoteEngine``); otherwise
it starts the Core in this process (the same start as ``puffpai serve``), so
Toolsmith's own process can reach it. First it offers the system parts (the
Desktop, the wizard, the cloud avatar), each with its own Yes card shown here
(``builtins``). No app is added at setup (lane I10c): the apps that ship
with BuildBot (FreshTable, the example page) are in the PuffPAI store on the
Desktop, and the plan card may suggest one for a job. After the wizard the Core keeps running until Ctrl+C, unless
``--exit`` was given. When setup finished, the chat opens right here, on this Core (D94: the terminal chat is the
default way in, the dashboard an option); when she leaves it, BuildBot keeps running in this window.

Stop BuildBot works through all of it (D122). :class:`Answers` reads her answers on a thread of their own, so this
window sees a stop while it waits at a prompt, and setup then ends through its usual stop. A step's own work (a vault
being made, a system part being added) is never cut in two: the stop is seen when that work is done.

While Toolsmith waits for a locked vault, the window with its own Core and a real terminal asks for the PIN right there
(D142), with no echo, and sends it to ``POST /v1/vault/unlock`` as the chat's /unlock does. It never passes through the
engine or ``/v1/wizard`` (D106): the engine only says, on every poll of the wait, that the vault is what it waits for.
"""

from __future__ import annotations

import argparse
import getpass
import os
import shlex
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, TextIO

from . import lines as L

try:
    import termios
except ImportError:                           # Windows: the console's own mode instead (_echo_back)
    termios = None

CLEAR = "\x1b[3J\x1b[2J\x1b[H"
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


# Harvested from PuffPAIBuildBot@15bad54b:src/puffpai/wizard/render_cli.py:327-347 (copied: _windows_vt_on)
def windows_vt_on(stream: Any) -> bool:
    """Turn on escape-code handling for this console. False when it can't.

    A classic Windows console prints ``ESC[3J`` as junk unless asked to read it, so on Windows the codes
    are written only once the console has agreed. Off Windows this is always True."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        wanted = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return wanted == mode.value or bool(kernel32.SetConsoleMode(handle, wanted))
    except Exception:  # noqa: BLE001 - can't tell: write nothing
        return False


def is_tty(stream: Any) -> bool:
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001
        return False


def is_console(stream: Any) -> bool:
    """A terminal someone can type a secret at (D142). On Windows ``isatty`` is True for any character device, NUL too,
    and getpass on NUL spins for ever, so there it takes a real console: GetConsoleMode has to work on the handle."""
    if not is_tty(stream):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(stream.fileno()))
        return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(ctypes.c_uint32())))
    except Exception:  # noqa: BLE001 - can't tell: not asked
        return False


class SetupStopped(BaseException):
    """Stop BuildBot was pressed while this window waited (D122). Not an ``Exception``, and not a Ctrl+C: nothing on
    the way out mistakes it for a failed step or for her leaving the chat."""


class Overtaken(Exception):
    """The question this window asks is not the one the engine asks now (D122). ``screen``: the engine's screen now."""

    def __init__(self, screen: dict) -> None:
        super().__init__("overtaken")
        self.screen = screen


def _conin_mode(set_to: int | None = None) -> int | None:
    """Windows: this process's console input mode (CONIN$, whatever stdin is), after setting it to *set_to* if given.
    None: no console to ask. OSError: none to open."""
    import ctypes
    import msvcrt
    fd = os.open("CONIN$", os.O_RDWR)
    try:
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(fd))
        kernel32 = ctypes.windll.kernel32
        if set_to is not None:
            kernel32.SetConsoleMode(handle, ctypes.c_uint32(set_to))
        mode = ctypes.c_uint32()
        return mode.value if kernel32.GetConsoleMode(handle, ctypes.byref(mode)) else None
    finally:
        os.close(fd)


def _echo_back() -> Callable[[], None] | None:
    """How to give this terminal the settings it has now (D142). getpass changes them while it reads and changes them
    back itself, which never happens for a read left behind (:func:`_leave`). Off Windows it turns echo off. On Windows
    its getwch sets the console's input mode to 0, and a later program in that window then has no echo and no line
    input. None: nothing to give back. The flush on the way back drops a half-typed PIN, or it goes to her shell."""
    if termios is None:
        if os.name != "nt":
            return None
        try:
            mode = _conin_mode()
        except Exception:  # noqa: BLE001 - no console: getpass has no mode of this one to change
            return None
        if mode is None:
            return None

        def give_console_back() -> None:
            try:
                _conin_mode(mode)
            except Exception:  # noqa: BLE001 - on the way out, with a console that went away
                pass
        return give_console_back
    try:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except Exception:  # noqa: BLE001 - not a terminal: getpass has no echo of this one to turn off
        return None

    def give_back() -> None:
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, saved)
        except Exception:  # noqa: BLE001 - on the way out, with a terminal that went away
            pass
    return give_back


class Answers:
    """Her answers in the setup window, read on a thread of their own (D122), so a read that blocks can't hold up a
    stop. Windows gives no plain way to cancel a read of the console, so a read still out when setup ends is left
    behind: see :func:`_leave`. A read left out by an overtaken question takes the answer to the next question, except
    where a secret is in it either way (D142): a secret is never read through a read that shows what she types, and
    what a hidden read took is never the answer to anything else."""

    EVERY_S = 0.2

    def __init__(self, stopped: Callable[[], bool], read_line: Callable[[str], str] | None = None,
                 read_hidden: Callable[[str], str] | None = None, out: TextIO | None = None) -> None:
        self.stopped = stopped
        self._read_line = read_line or input
        self._read_hidden = read_hidden or getpass.getpass
        self._out = out
        self._box: queue.SimpleQueue | None = None      # the read still out, if any
        self._secret_out = False                        # that read is a hidden one
        self._echo: Callable[[], None] | None = None    # how to give the terminal back from a hidden read

    @property
    def read_is_out(self) -> bool:
        return self._box is not None and self._box.empty()

    def line(self, prompt: str, watch: Callable[[], None] | None = None, watch_s: float = 2.0) -> str:
        return self._wait(self._read_line, prompt, watch, watch_s)

    def hidden(self, prompt: str, watch: Callable[[], None] | None = None, watch_s: float = 2.0) -> str:
        return self._wait(self._read_hidden, prompt, watch, watch_s, secret=True)

    def give_echo_back(self) -> None:
        """For a hidden read left behind as setup ends (:func:`_leave`): her terminal is not left with no echo."""
        give_back, self._echo = self._echo, None
        if give_back is not None and self._secret_out and self.read_is_out:
            give_back()

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(left, self.EVERY_S))

    def check(self) -> None:
        if self.stopped():
            raise SetupStopped

    def _wait(self, read: Callable[[str], str], prompt: str, watch: Callable[[], None] | None, watch_s: float,
              secret: bool = False) -> str:
        self.check()
        out = self._out or sys.stdout
        if self.read_is_out and (secret or self._secret_out):
            out.write(f"\n  {L.HIDDEN_READ_OUT}\n" if self._secret_out else f"  {L.PIN_ENTER_FIRST}\n")
            out.flush()
        while self.read_is_out and (secret or self._secret_out):      # D142: that read ends first, and its line is dropped
            time.sleep(self.EVERY_S)
            self.check()
        if self.read_is_out:
            out.write(prompt)
            out.flush()
        else:                                           # a line that came for an overtaken question is dropped
            self._secret_out = secret
            self._echo = _echo_back() if secret else None
            self._box = queue.SimpleQueue()
            threading.Thread(target=self._read, args=(self._box, read, prompt), name="puffpai-setup-read",
                             daemon=True).start()
        box = self._box
        look_at = time.monotonic() + watch_s
        while True:
            try:
                ok, value = box.get(timeout=self.EVERY_S)
            except queue.Empty:
                self.check()
                if watch is not None and time.monotonic() >= look_at:
                    watch()
                    look_at = time.monotonic() + watch_s
                continue
            self._box = None
            self.check()
            if ok:
                return value
            raise value

    @staticmethod
    def _read(box: queue.SimpleQueue, read: Callable[[str], str], prompt: str) -> None:
        try:
            box.put((True, read(prompt)))
        except BaseException as exc:  # noqa: BLE001 - EOF or a Ctrl+C in the read: for the main thread to raise
            box.put((False, exc))


def _leave(answers: Answers, code: int) -> int:
    """The way out of ``puffpai setup``, with everything already stopped. A read still out can't be cancelled, and
    ending Python with one on a pipe can crash on its way out, so this process ends here, with its output written."""
    if answers.read_is_out:
        sys.stdout.flush()
        sys.stderr.flush()
        answers.give_echo_back()              # last: the read left behind can change it again with a key she presses
        os._exit(code)
    return code


class Terminal:
    def __init__(self, engine: Any, out: TextIO | None = None, read_line: Callable[[str], str] | None = None,
                 read_hidden: Callable[[str], str] | None = None, env: dict | None = None,
                 run: Callable[..., Any] = subprocess.run, sleep: Callable[[float], None] = time.sleep,
                 tty: Callable[[Any], bool] = is_tty, vt_on: Callable[[Any], bool] = windows_vt_on,
                 answers: Answers | None = None, unlock: Callable[[str], tuple[bool, str]] | None = None) -> None:
        self.tty, self.vt_on = tty, vt_on
        self.engine = engine
        self.answers = answers
        self.sleep = answers.sleep if answers else sleep
        self.out = out or sys.stdout
        self._read_line = answers.line if answers else (read_line or input)
        self._read_hidden = answers.hidden if answers else (read_hidden or getpass.getpass)
        self.env = os.environ if env is None else env
        self.run_cmd = run
        self._printed: set[str] = set()       # the lines this window already shows
        self._held: list[str] = []            # lines that came while a question waited, shown after it
        self._look: tuple | None = None       # how the question now asked looks whether it still stands (D122)
        self.unlock = unlock                  # D142: opens the vault from a PIN typed here. None: only the line, today's way
        self._pin_settled = False             # this wait for the vault: she skipped, or it was opened from here
        self._pin_told = False                # this wait: she was told to press Enter first

    # -- input ----------------------------------------------------------------
    def read(self, field: dict, prompt: str) -> str:
        """One answer. Hidden fields never echo."""
        if field.get("hidden"):
            return self._read_hidden(prompt)
        if self._look is not None and self.answers is not None:
            return self.answers.line(prompt, *self._look)
        return self._read_line(prompt)

    def write(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    # Harvested from PuffPAIBuildBot@15bad54b:src/puffpai/wizard/render_cli.py:350-393 (adapted: clear_terminal)
    def clear(self) -> bool:
        """Clear the screen and its scroll history where the terminal allows it. The codes are written only to
        a terminal whose console reads them, so a pipe, a log or a classic console never gets junk; it never
        raises. True when they were written (still no promise that the scroll history is gone)."""
        wrote = False
        try:
            if self.tty(self.out) and self.vt_on(self.out):
                self.out.write(CLEAR)
                self.out.flush()
                wrote = True
        except Exception:  # noqa: BLE001 - not cleared: the owner is told
            wrote = False
        if self.env.get("TMUX"):
            pane = self.env.get("TMUX_PANE")
            cmd = ["tmux", "clear-history"] + (["-t", pane] if pane else [])
            try:
                self.run_cmd(cmd, check=False, capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        return wrote

    # -- drawing --------------------------------------------------------------
    def _lines(self, screen: dict) -> None:
        # One window shows every screen one after another: a line it already shows (the plan list after
        # an answer, again under "One Yes") is not printed twice.
        for line in screen.get("lines") or []:
            if line in self._printed and not screen.get("summary") and line not in (screen.get("repeat") or []):
                continue                                              # a summary lists every line again
            self._printed.add(line)
            self.write(f"  {line}")

    def _field(self, field: dict, error: str | None) -> str:
        if error:
            self.write(f"  ! {error}")
        self.write(f"  {field['label']}")
        if field.get("help"):
            self.write(f"    {field['help']}")
        if field.get("example_line"):
            self.write(f"    {field['example_line']}")
        options = field.get("options")
        if options:
            for i, opt in enumerate(options, 1):
                note = f"  ({opt['note']})" if opt.get("note") else ""
                self.write(f"    {i}. {opt['label']}{note}")
        if field.get("hint"):
            self.write(f"    {field['hint']}")
        raw = self.read(field, "  > ").strip()
        if options and raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]["value"]
        return raw

    def run_step(self, step_id: str) -> dict:
        screen = self.engine.submit(step_id, {}, renderer="terminal")
        self.write("")
        self.write(f"== {screen['title']}")
        while True:
            if screen.get("clear"):
                cleared = self.clear()
                self.write(f"== {screen['title']}")
                if cleared:
                    self._printed = set()          # a fresh screen shows its lines again
                else:
                    self.write(f"  {L.NOT_CLEARED}")
            self._lines(screen)
            if screen.get("show"):
                self.write("")
                self.write(f"    {screen['show']['value']}")
                self.write("")
                self.write(f"  {screen['show']['line']}")
            # D142. Any screen with no vault on it ends the wait, so a later lock asks again. A card in the middle of a
            # wait carries vault too, so it neither asks (she answers the card first) nor forgets that she skipped.
            if not screen.get("vault"):
                self._pin_settled = self._pin_told = False
            if screen["status"] == "working":
                if screen.get("vault") and self.unlock is not None and not self._pin_settled:
                    seen = self._ask_pin(step_id, float(screen.get("poll_s") or 2))
                    if seen is not None:
                        screen = seen
                        continue
                self.sleep(float(screen.get("poll_s") or 2))
                screen = self.engine.submit(step_id, {}, renderer="terminal")
                continue
            if screen["status"] not in ("ask", "show") or not screen.get("field"):
                return screen
            field = screen["field"]
            watch = self._watch(step_id, screen)
            self._look = (watch, float(screen.get("poll_s") or 2)) if watch else None
            try:
                answer = self._field(field, screen.get("error"))
            except Overtaken as moved:
                self.write("")                     # the cursor is still on the prompt's line
                self.write(f"  {L.ANSWERED_ELSEWHERE}")
                screen = moved.screen
                continue
            screen = self.engine.submit(step_id, {field["id"]: answer}, renderer="terminal")
            self._held, screen["lines"] = [], self._held + list(screen.get("lines") or [])

    def _watch(self, step_id: str, screen: dict) -> Callable[[], None] | None:
        """D122. A build's question can be answered on the Desktop, or end with its job, while this window waits. So
        while it waits it asks the engine again, the same poll as the build's own, and goes on when the question is
        gone. Lines that come meanwhile wait for the next screen, so nothing is written over what she types."""
        asked = screen.get("asked")
        if not asked or self.answers is None:
            return None

        def look() -> None:
            now = self.engine.submit(step_id, {}, renderer="terminal")
            lines = list(now.get("lines") or [])
            self._printed.difference_update(now.get("repeat") or [])
            if not now.get("vault"):
                self._pin_settled = False      # D142: the wait ended behind this question
            if now.get("status") == "ask" and now.get("asked") == asked:
                self._held += lines
                return
            self._held, now["lines"] = [], self._held + lines
            raise Overtaken(now)
        return look

    def _ask_pin(self, step_id: str, poll_s: float) -> dict | None:
        """D142. Ask for the PIN once, under the engine's line. A read the owner can see is out (an overtaken question,
        D122): reading now would show the PIN on her screen, so she is told to press Enter first and a later poll asks.
        Returns the engine's latest screen if it looked while she typed, else None. The PIN goes to ``self.unlock`` and
        nowhere else. After a refusal it asks again at once, with no sleep or poll between: she retypes straight away,
        and until the next hidden read what she types shows. Every re-ask waits on her, so this never spins."""
        if self.answers is not None and self.answers.read_is_out:
            if not self._pin_told:
                self._pin_told = True
                self.write(f"  {L.PIN_ENTER_FIRST}")
            return None
        self._pin_told = False
        seen: list[dict] = []
        while True:
            self.write(f"  {L.PIN_HERE}")
            try:
                if self.answers is None:
                    typed = self._read_hidden("  > ")
                else:
                    typed = self.answers.hidden("  > ", self._pin_look(step_id, seen), poll_s)
            except (EOFError, OSError, ValueError):   # as the chat's /unlock: nothing typed
                typed = ""
            if seen and not seen[-1].get("vault"):
                typed = ""                            # the wait ended while she typed: never sent, never an answer
                break
            if not typed:
                self._pin_settled = True
                self.write(f"  {L.PIN_SKIPPED}")
                break
            opened, words = self.unlock(typed)
            typed = ""
            self.write(f"  {words}")
            if opened:
                self._pin_settled = True              # the job can still say it waits for a moment: not asked again
                break
            if seen and seen[-1].get("status") != "working":
                break                                 # a card came while she typed: it goes first
        if not seen:
            return None
        self._held, seen[-1]["lines"] = [], self._held
        return seen[-1]

    def _pin_look(self, step_id: str, seen: list[dict]) -> Callable[[], None]:
        """The poll while the PIN is asked. A read can't be cancelled, so the screens wait in *seen* and their lines in
        ``_held``: no job line is lost and nothing is written over the prompt, but the one line that the wait ended.
        That screen is what comes next, so the engine is not asked again until she presses Enter."""

        def look() -> None:
            if seen and not seen[-1].get("vault"):
                return
            try:
                now = self.engine.submit(step_id, {}, renderer="terminal")
            except Exception:  # noqa: BLE001 - the poll after this read says it: this read is never left out for it
                return
            self._printed.difference_update(now.get("repeat") or [])
            self._held += list(now.get("lines") or [])
            seen[:] = [now]
            if not now.get("vault"):
                self.write("")                        # the cursor is still on the prompt's line
                self.write(f"  {L.PIN_NOT_NEEDED}")
        return look

    def run(self) -> int:
        overview = self.engine.overview()
        for row in overview["steps"]:
            if row["state"] == "done":
                continue
            self.run_step(row["id"])
        return 0 if self.engine.overview()["complete"] else 1


class RemoteEngine:
    """The engine of a Core that is already running, through its Core API (``/v1/wizard``)."""

    def __init__(self, call: Callable[[str, str, dict | None], tuple[int, Any]]) -> None:
        self.call = call

    def overview(self) -> dict:
        status, body = self.call("GET", "/v1/wizard", None)
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"The running Core did not show its setup steps (HTTP {status}).")
        return body

    def submit(self, step_id: str, answers: dict | None = None, renderer: str = "terminal") -> dict:
        status, body = self.call("POST", f"/v1/wizard/{step_id}", {"answers": answers or {}, "renderer": renderer})
        if status != 200 or not isinstance(body, dict):
            error = body.get("error") if isinstance(body, dict) else str(body)
            return {"status": "waiting", "step": step_id, "title": step_id, "lines": [str(error)], "answered": []}
        return body


#: The system parts that ship with PuffPAI and are offered at setup, in this order (10_BUILD_PLAN item 1).
#: The built-in apps are not here: they are in the PuffPAI store on the Desktop (lane I10c).
BUILTINS = ("desktop", "wizard", "cloud-avatar")


def moved_lines(said: dict) -> list[str]:
    """What she does next on a moved bot, in plain words (D141)."""
    from ..bot_move import STAYS
    out = []
    for key, head in (("linked", "Link these folders again, if you have them here"), ("pinned", "Pin these documents again"),
                      ("documents", "Add these documents again"),
                      ("books", "These books can come back from Recommended for this bot, on Knowledge"),
                      ("topics", "Books and articles on these can come with one Yes, from Recommended for this bot, on Knowledge"),
                      ("added", "Add these tool servers and APIs again")):
        if said.get(key):
            out.append(f"{head}: {', '.join(said[key])}.")
    if said.get("not_here"):
        out.append(f"These were set up on another kind of computer, so they were not added. Ask Toolsmith to try them here: {', '.join(said['not_here'])}.")
    for row in said.get("left_out") or []:
        out.append(f"{row.get('name')} did not come: {row.get('why')}." + (f" It came from {row['from']}." if row.get("from") else ""))
    return out + ["This didn't travel, so it is entered again here:"] + [f"  - {s}" for s in STAYS]


def _still_to_add(core: Any, pid: str) -> bool:
    """Not on this bot yet, or added and broken (its install did not finish)."""
    row = core.registry.get(pid)
    return row is None or row.get("status") == "broken"


def add_moved_apps(core: Any, record: dict, ask: Callable[[str], str], write: Callable[[str], None],
                   *, again: str = "", repair: bool = False) -> list[str]:
    """Each app of a moved bot through the Package Manager, with its own Yes card, as :func:`builtins`. Returns those
    added. A broken one is taken out first, its data kept, and added again. *again* is the command that runs this again.
    *repair*: setup finished earlier, so her look stays as it is now."""
    from .. import bot_move
    from ..consent import card_sha, card_view
    from ..packages.manager import AddError
    done = []
    apps = bot_move.to_add(record)
    needs_way_out = any(_still_to_add(core, str(a.get("id"))) for a in apps)
    bridge = getattr(getattr(core, "zone3", None), "bridge", None)

    def closed() -> bool:
        return bridge is not None and bridge.view().get("state") != "open"

    def not_added(name: Any, why: str) -> None:
        write(f"  {name} was not added: {why}")
        if closed():
            write(f"  The Bridge is closed, and {name} may need it to download what it needs. "
                  f"To try again and open it, run: {again or 'puffpai setup'}")

    if needs_way_out and closed():
        if ask("  This bot's main apps can't install while the Bridge is closed, because they download what they need. "
               "Open it for this? [Y/n] ").strip().lower() not in ("n", "no"):
            bridge.open(by="owner")
    for app in apps:
        pid = str(app.get("id"))
        if not _still_to_add(core, pid):
            continue
        try:
            if core.registry.get(pid) is not None:
                core.packages.remove(pid, keep_data="keep")
            staged = core.packages.add(bot_move.app_source(record, app))
        except AddError as exc:
            not_added(app.get("name"), " ".join(exc.problems))
            continue
        seen = card_view(staged["card"], staged["floor"])
        write("")
        write(f"  {seen['title']}")
        for line in seen["lines"]:
            write(f"    {line}")
        if ask("  Say Yes to this? [y/N] ").strip().lower() not in ("y", "yes") or staged["floor"]:
            core.consent.answer(staged["consent_id"], "no", by="owner")
            write("  Not added. You can add it later from Recommended for this bot, on Knowledge.")
            continue
        core.consent.answer(staged["consent_id"], "yes", "tap", by="owner", card_sha=card_sha(staged["card"]))
        result = core.packages.wait(staged["consent_id"], timeout_s=1800)
        if result.get("status") == "installed":
            done.append(pid)
            write(f"  {app.get('name')} is added.")
        else:
            not_added(app.get("name"), " ".join(result.get("problems") or []))
    if not repair:
        bot_move.finish_look(core.state, record, core.registry)
    return done


def take_moved(core: Any, folder: str) -> tuple[dict, bool]:
    """The moved bot's record, and whether setup had finished already. Its name, look and the rest go on only the first
    time: after that they are hers, and running setup again must not put them back."""
    from .. import bot, bot_move
    moved = bot_move.read(folder)
    if bot._set_up(core.state):
        return moved, True
    for line in bot_move.apply(core.state, moved, core.registry):
        print(line, flush=True)
    bot_move.seed_setup(core.state)
    print(f"Moving {(moved.get('bot') or {}).get('name') or 'your bot'} here. Setup asks only what can't travel.", flush=True)
    return moved, False


def builtins(core: Any, ask: Callable[[str], str], write: Callable[[str], None]) -> list[str]:
    """Add each built-in package that is not installed yet through the Package Manager: its Yes card
    is shown here (``consent.card_view``) and she answers it. Returns the ids installed now."""
    from ..consent import card_sha, card_view
    from ..desktop import builtin_dir
    from ..packages.manager import AddError
    done = []
    for pid in BUILTINS:
        folder = builtin_dir() / pid
        if core.registry.get(pid) is not None or not folder.is_dir():
            continue
        try:
            staged = core.packages.add(str(folder))
        except AddError as exc:
            write(f"  {pid} was not added: {' '.join(exc.problems)}")
            continue
        seen = card_view(staged["card"], staged["floor"])
        write("")
        write(f"  {seen['title']}")
        for line in seen["lines"]:
            write(f"    {line}")
        if ask("  Say Yes to this? [y/N] ").strip().lower() not in ("y", "yes") or staged["floor"]:
            core.consent.answer(staged["consent_id"], "no", by="owner")
            write(f"  Not added. You can add it later with: puffpai add {folder}")
            continue
        core.consent.answer(staged["consent_id"], "yes", "tap", by="owner", card_sha=card_sha(staged["card"]))
        result = core.packages.wait(staged["consent_id"], timeout_s=1800)
        if result.get("status") == "installed":
            from ..cli import status_word                  # the same words as puffpai list
            row = core.registry.get(pid) or {}
            word = status_word(row, core.registry) if row.get("id") else "installed"
            write(f"  Added {row.get('name') or pid} ({word or 'installed'}).")
            done.append(pid)
        else:
            write(f"  {pid} was not installed: {' '.join(map(str, result.get('problems') or []))}")
    return done


def _running_core() -> dict | None:
    from ..cli import _terminal
    return _terminal()


def main(argv: list[str] | None = None) -> int:
    """``puffpai setup``: the system parts, then the wizard in this terminal, on a running Core."""
    parser = argparse.ArgumentParser(prog="puffpai setup")
    parser.add_argument("--port", type=int)
    parser.add_argument("--pages-port", type=int)
    parser.add_argument("--proxy-port", type=int)
    parser.add_argument("--exit", action="store_true", help="stop the Core when setup ends")
    parser.add_argument("--from", dest="from_folder", default=None, help="a bot moved from another computer (D141)")
    args = parser.parse_args(argv or [])
    info = _running_core()
    if info is not None:
        from ..cli import _call
        print(f"BuildBot is already running at http://puffpai.localhost:{info['port']}; setup uses it.")
        if args.from_folder:
            print("BuildBot is running already. Stop it first and run setup again, or use: puffpai import <folder>", flush=True)
            return 1
        return _setup_remote(info, _call)

    from .. import corelock
    from ..cli import fill_ports
    from ..paths import state_dir
    fill_ports(args)                          # D129: this bot's own ports, picked and saved the first time
    try:                                      # D91: one Core per bot; held until this Core has stopped
        lock = corelock.acquire(state_dir(), port=args.port, pages_port=args.pages_port,
                                wait_s=corelock.START_WAIT_S)
    except corelock.LockError as exc:
        for line in exc.lines():
            print(line)
        if isinstance(exc, corelock.AlreadyRunning) and exc.info.get("kind") != corelock.TOOL:
            print("Open Setup in its Desktop to go on with setup there.")
        return 1
    answers = Answers(lambda: False)
    try:
        code = _setup_core(args, answers)
    finally:
        lock.release()
    return _leave(answers, code)


def core_unlock(pin: str) -> tuple[bool, str]:
    """The PIN typed in the setup window (D142), on the same path and with the same words as the chat's /unlock
    (``termchat.Chat._unlock``): this Core's route with its terminal key. Its own limits and its own words apply. Says
    whether the vault opened, and the words for her. Those words never hold the PIN or an exception's text."""
    from ..cli import _call, _terminal
    from ..termchat import said
    body = {"method": "pin", "secret": pin}
    pin = ""
    try:
        info = _terminal()
        if info is None:
            return False, L.PIN_DID_NOT_OPEN.format(why=L.PIN_NOT_REACHED)
        status, got = _call(info, "POST", "/v1/vault/unlock", body)
    except Exception:  # noqa: BLE001 - a Core that stopped answering: the next poll or stop says the rest
        return False, L.PIN_DID_NOT_OPEN.format(why=L.PIN_NOT_REACHED)
    finally:
        body.clear()                          # the PIN goes once, in this one request
    if status != 200:
        return False, L.PIN_DID_NOT_OPEN.format(why=said(got, L.PIN_NOT_TAKEN))
    return True, L.PIN_OPENED


def _setup_remote(info: dict, call: Callable[..., tuple[int, Any]]) -> int:
    """``puffpai setup`` on a Core another window runs. When that Core stops (D122), this window ends too: the Core's
    stop removes its terminal key last, and a Core that stopped answering counts the same."""
    from ..api.app import STOPPED_LINE
    from ..paths import state_dir
    from ..termchat import GONE_ERRORS
    key = state_dir() / "settings" / "terminal.json"
    answers = Answers(lambda: not key.exists())
    code = 1
    try:
        code = Terminal(RemoteEngine(lambda m, path, body: call(info, m, path, body)), answers=answers).run()
    except (SetupStopped, *GONE_ERRORS):
        if answers.read_is_out:               # the cursor is still on the prompt's line
            print()
        print(STOPPED_LINE, flush=True)
        for line in setup_stopped_lines():
            print(line, flush=True)
    except KeyboardInterrupt:
        print()
    return _leave(answers, code)


def _setup_core(args: argparse.Namespace, answers: Answers) -> int:
    """``puffpai setup`` with its own Core, in this process, while :func:`main` holds the bot's lock (D91)."""
    from .engine import Engine
    from .services import default_services
    from ..api.app import (STOPPED_LINE, accept_shutdown, break_stops, create_app, end_streams, make_servers,
                           run_servers, start_core, stop_core, stop_servers, stop_toolsmith, stopping)
    app = create_app(port=args.port, pages_port=args.pages_port)
    core = app.extensions["puffpai"]
    core.proxy_port = args.proxy_port
    try:
        servers = make_servers(app, args.port, args.pages_port)
    except OSError as exc:
        why = getattr(exc, "strerror", None) or str(exc)
        print(f"Setup can't start BuildBot: port {args.port} or {args.pages_port} is in use by another program "
              f"({why}).")
        return 1
    terminal = start_core(app)                # the same start as puffpai serve (Zone 3, wiring, Toolsmith)
    break_stops()
    # D122: puffpai shutdown works from here on. The listeners close as soon as it is asked (run_servers), and this
    # thread sees it at its next prompt or poll (Answers), then runs the one stop below.
    stop = accept_shutdown(app)
    answers.stopped = stop.is_set
    thread = threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, name="puffpai-setup-core",
                              daemon=True)
    thread.start()
    code = 1
    stopped_here = False
    finished = False                          # every step, and a moved bot's apps too
    how_stopped = L.SETUP_STOPPED
    again = setup_again_command(args.from_folder)
    try:
        print(f"BuildBot is running at http://puffpai.localhost:{args.port} while setup runs.", flush=True)
        print("System parts (the Desktop, setup, the cloud avatar): each one shows its Yes card first.")
        builtins(core, answers.line, print)
        moved = None
        repair = False
        if args.from_folder:
            from .. import bot_move
            try:
                moved, repair = take_moved(core, args.from_folder)
            except bot_move.MoveRefused as exc:
                print(f"Not set up from that folder: {exc}", flush=True)
                return 1
        with app.app_context():
            from ..api.groups import wizard as wizard_group
            engine = wizard_group.engine()    # the browser's engine too: one progress, two renderers
        if not isinstance(engine, Engine):
            engine = Engine(core.state, default_services(core), registry=core.registry, bus=core.bus)
        # D142: only a real terminal is asked. getpass on a pipe falls back to showing what is typed, and spins on NUL.
        unlock = core_unlock if is_console(sys.stdin) and is_console(sys.stdout) else None
        code = Terminal(engine, answers=answers, unlock=unlock).run()
        if moved is not None and code == 0:
            from .. import bot_move
            add_moved_apps(core, moved, answers.line, print, again=again, repair=repair)
            if not repair:
                for line in moved_lines(bot_move.lines_for(moved)):
                    print(line, flush=True)
        finished = code == 0
        if not args.exit:
            from .. import desktop
            if code == 0:
                setup_done(args.port, core.state, _bot_name(core), read_line=answers.line,
                           chat=lambda: _chat(answers))
            answers.check()                   # a chat that ended because the Core stopped
            for line in running_lines(args.port, desktop.dashboard_link(core.state, args.port).code):
                print(line, flush=True)
            while thread.is_alive():          # a join with no timeout never sees Ctrl+C on Windows
                thread.join(0.5)
    except SetupStopped:
        stopped_here = True
        if answers.read_is_out:               # the cursor is still on the prompt's line
            print(flush=True)
    except KeyboardInterrupt:
        stopped_here = True
        how_stopped = L.SETUP_INTERRUPTED
        print(flush=True)                     # the cursor can still be on a prompt's line; on Windows the read has ended
    except EOFError:
        print(f"Setup stopped: there were no more answers. Run {again or 'puffpai setup'} again to go on where you left off.")
    finally:
        with stopping():
            end_streams(app)
            stop_toolsmith(core)              # a request waiting on Toolsmith ends before the listeners close
            stop_servers(servers)
            thread.join(15)
            stop_core(core, terminal)
        print(STOPPED_LINE, flush=True)
    if stopped_here and not finished:
        for line in setup_stopped_lines(args.from_folder, how_stopped):
            print(line, flush=True)
    return code


def _chat(answers: Answers) -> int:
    from .. import termchat
    return termchat.main(read_line=answers.line, read_hidden=answers.hidden)


def _bot_name(core: Any) -> str:
    """The bot's own name (``bot.read``, the one the chat shows), else "BuildBot"."""
    try:
        from .. import bot
        return bot.read(core.state, core.registry)["name"] or "BuildBot"
    except Exception:  # noqa: BLE001 - no name to read: the plain one
        return "BuildBot"


def setup_done(port: int, state: Path, name: str, read_line: Callable[[str], str] = input,
               write: Callable[[str], None] = print, open_url: Callable[[str], Any] | None = None,
               chat: Callable[[], Any] | None = None) -> None:
    """The end of setup (D94, the owner's change of 10-30: the window is the default). Enter opens the PuffPAI window,
    with its shortcuts so she can open it again (``puffpai app --no-shortcuts`` removes them), and does not block
    this terminal. ``d`` opens the dashboard with a one-time pair link. Then this terminal goes on into the chat, on
    the Core this setup started (``termchat.main``, with the terminal key ``start_core`` wrote); ``t`` goes straight
    there. A window that can't open says so in one plain line, and the chat is there. No more input (EOF) or Ctrl+C
    here skips the chat. Ctrl+C, EOF or /quit in the chat ends it; the caller then says BuildBot runs in this window
    (``running_lines``)."""
    import webbrowser
    from .. import termchat
    write("")
    write(f"  Setup is done. {name} is running.")
    write("")
    write(f"  Your dashboard is ready too:  http://puffpai.localhost:{port}   (in your browser)")
    from .. import desktop
    link = desktop.dashboard_link(state, port)         # D79: the Desktop's own name; D108: a code only for our own
    for line in pair_lines(state, port, link):
        write(f"  {line}")
    write("")
    try:
        key = read_line("  [Enter] open the window   [d] open the dashboard   [t] chat here  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        write("")
        return
    if key == "d":
        try:
            (open_url or webbrowser.open_new_tab)(link.url)
        except Exception:  # noqa: BLE001 - no browser: the link is said, she can open it herself
            pass
        write(f"  Your dashboard: {link.url}")
        write(f"  {termchat.PAIR_ONCE if link.code else link.line}")
        write("")
    if key == "":
        from .. import window
        if os.name == "nt" and not window.why_not():           # no shortcuts to a window that can't open here
            write(f"  {window.make_shortcuts(state, name, port=port)[1]}")
        write(f"  {window.open_window(port)[1]}")
        write("")
    try:
        (chat or termchat.main)()
    except KeyboardInterrupt:              # Ctrl+C before the chat's own prompt: it ends here too
        write("")


#: The launcher files, by their Windows names (install.ps1). Off Windows, install.sh writes these (D132).
POSIX_LAUNCHERS = {"Start BuildBot.cmd": "start-buildbot.sh", "Stop BuildBot.cmd": "stop-buildbot.sh",
                   "Chat with BuildBot.cmd": "chat-buildbot.sh", "Sign in a browser.cmd": "signin-buildbot.sh"}


def launcher_name(name: str) -> str:
    """The file name of one launcher on this computer."""
    return name if os.name == "nt" else POSIX_LAUNCHERS[name]


def how_to_run(path: Path) -> str:
    """The words for running one launcher, true to this computer: Windows double-clicks a file, a terminal runs it."""
    if os.name == "nt":
        return f"double-click {path.name} in {path.parent}"
    # A bot in a container (scripts/container/run.sh): she is on the computer outside it, so the line has to go in first.
    outside = os.environ.get("PUFFPAI_HOST_EXEC", "").strip()
    return f"run {outside + ' ' if outside else ''}{shlex.quote(str(path))}"


def launcher() -> Path:
    """``Start BuildBot.cmd``, which install.ps1 writes next to the code (off Windows, install.sh's own name), or in
    ``bots\\<name>\\`` for a bot that is not the first from this folder (D129). The other launchers sit beside it."""
    from ..paths import bot_name
    repo = Path(__file__).resolve().parents[3]
    name = launcher_name("Start BuildBot.cmd")
    own = repo / "bots" / bot_name() / name
    return own if own.is_file() else repo / name


def stop_launcher() -> Path:
    """``Stop BuildBot.cmd`` (lane I7), which install.ps1 writes next to ``Start BuildBot.cmd``."""
    return launcher().with_name(launcher_name("Stop BuildBot.cmd"))


def chat_launcher() -> Path:
    """``Chat with BuildBot.cmd`` (D86), which install.ps1 writes next to ``Start BuildBot.cmd``."""
    return launcher().with_name(launcher_name("Chat with BuildBot.cmd"))


def cli_command(*args: str) -> str:
    """One command that really runs here, for a bot with no launcher files (run from source). ``puffpai`` is on no
    PATH (D92(e)), so bare ``puffpai`` works in no terminal: this names this install's own program, and this bot when
    it is not the default one."""
    exe = Path(sys.executable).with_name("puffpai.exe" if os.name == "nt" else "puffpai")
    if os.name != "nt":
        run = shlex.quote(str(exe)) if exe.is_file() else f"{shlex.quote(sys.executable)} -m puffpai_core.cli"
        named = next((f"{k}={shlex.quote(os.environ[k])} " for k in ("PUFFPAI_STATE", "PUFFPAI_BOT") if os.environ.get(k, "").strip()), "")
        return " ".join([named + run, *args])
    run = f'& "{exe}"' if exe.is_file() else f'& "{sys.executable}" -m puffpai_core.cli'
    named = next((f"$env:{k}='{os.environ[k].replace(chr(39), chr(39) * 2)}'; " for k in ("PUFFPAI_STATE", "PUFFPAI_BOT") if os.environ.get(k, "").strip()), "")
    return " ".join([named + run, *args])


def signin_launcher() -> Path:
    """``Sign in a browser.cmd`` (D92), which install.ps1 writes next to ``Start BuildBot.cmd``."""
    return launcher().with_name(launcher_name("Sign in a browser.cmd"))


def chat_line() -> str:
    """The Done step's one line on chatting with the bot in a terminal (D86), true to this install: the
    launcher where install.ps1 wrote it, else the command."""
    chat = chat_launcher()
    if chat.is_file():
        return f"To chat with your bot in a terminal, {how_to_run(chat)}."
    return f"To chat with your bot in a terminal, run this there while BuildBot runs: {cli_command()}"


def vault_wait_line() -> str:
    """The setup terminal's one line when Toolsmith waits for a locked vault (D113), true to this install: the two real
    ways out from where she is (D132, the owner's WritersBot build). Her Core may be this very setup, so starting
    anything is the wrong hint: a chat beside it has /unlock, and the Desktop's Building has a PIN box once the browser
    is signed in."""
    chat, signin = chat_launcher(), signin_launcher()
    second = "" if os.name == "nt" else "in a second terminal, "
    how_chat = how_to_run(chat) if chat.is_file() else f"run this in a terminal: {cli_command('chat')}"
    how_signin = how_to_run(signin) if signin.is_file() else f"run this in a terminal: {cli_command('pair', '--running')}"
    return (f"Your vault is locked, so Toolsmith is waiting. To unlock it, {second}{how_chat}, then type /unlock and "
            f"your PIN. Or open Building on your Desktop and type your PIN there (to sign a browser in first, "
            f"{how_signin}). This window goes on by itself when the vault is open.")


def pair_lines(state: Path, port: int, link: Any) -> list[str]:
    """What setup says with the Desktop's address (D132; v1 did it, and the owner missed it): the one-time link, the same
    code for another device on her home network, and how to get a new one. No code: the plain line ``dashboard_link`` gave."""
    from .. import termchat
    from ..api import network
    if not link.code:
        return [link.line] if link.line else []
    out = [f"To sign this browser in, open {link.url} {termchat.PAIR_ONCE}"]
    if network.saved_on(state):
        urls = network.urls_of(network.network_ports(port)[0], network.own_addresses(), network.own_names())
        if urls:
            out += [f"On another device on your home network, open one of these and type {link.code}:", *urls]
    signin = signin_launcher()
    how = how_to_run(signin) if signin.is_file() else f"run this in a terminal: {cli_command('pair', '--running')}"
    return out + [f"For a new code later, {how}."]


def start_again_line() -> str:
    start = launcher()
    if start.is_file():
        return f"To start it again later, {how_to_run(start)}."
    return f"To start it again later, run this in a terminal: {cli_command('serve')}"


def setup_again_command(from_folder: str | None = None) -> str:
    """The command that runs this same setup again, when plain setup is not it. An installer around setup names its own
    in ``PUFFPAI_SETUP_AGAIN``. A moved bot goes on from its folder."""
    own = os.environ.get("PUFFPAI_SETUP_AGAIN", "").strip()
    if own:
        return own
    if not from_folder:
        return ""
    return cli_command("setup", "--from", f'"{from_folder}"' if os.name == "nt" else shlex.quote(str(from_folder)))


def setup_stopped_lines(from_folder: str | None = None, first: str = L.SETUP_STOPPED) -> list[str]:
    """What the setup window says after setup was stopped before it was finished (D122), true to this install."""
    start = launcher()
    again = setup_again_command(from_folder)
    if again:
        how = f"To go on, run this in a terminal: {again}"
    elif start.is_file():
        how = f"To go on, {how_to_run(start)}, then open Setup."
    else:
        how = f"To go on, run this in a terminal: {cli_command('setup')}"
    return [first, how]


def stop_from_elsewhere_line() -> str:
    """How to stop BuildBot from outside its window (``puffpai shutdown``)."""
    stop = stop_launcher()
    if stop.is_file():
        return f"Or {how_to_run(stop)} to stop it from anywhere."
    return f"Or run this in another terminal to stop it from anywhere: {cli_command('shutdown')}"


def running_lines(port: int, pair_code: str) -> list[str]:
    """The last screen of setup, while BuildBot keeps running: that it runs, how to stop it, how to start it.
    (When setup runs from install.ps1, PowerShell ends the script at Ctrl+C, so these are said here.) No *pair_code*:
    the active Desktop is not PuffPAI's own, so no link (D108)."""
    return [
        "",
        "BuildBot is running now, in this window.",
        "Open this on this computer to see your Desktop (the link works once, for 10 minutes): "
        f"http://puffpai.localhost:{port}/#pair={pair_code}"               # D79: the Desktop's own name
        if pair_code else f"Your Desktop is at http://puffpai.localhost:{port}/",
        "To stop BuildBot, press Ctrl+C here, or close this window.",
        stop_from_elsewhere_line(),
        start_again_line(),
    ]
