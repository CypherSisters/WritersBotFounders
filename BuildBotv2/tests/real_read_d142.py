"""The real reads for D142, for ``test_d142_setup_pin.py``: real getpass and the real ``Answers`` and ``Terminal`` on a real
terminal, where a stand-in hid the fault. It needs only Python: ``terminal.py`` is loaded from the given core folder.

    real_read_d142.py CORE console OUT.json   Windows, in a real console: the input mode before, during and after a stop
    real_read_d142.py CORE nul OUT.json       Windows, stdin and stdout on NUL: what the check for a real terminal says
    real_read_d142.py CORE gap                off Windows, a real pty: a PIN retyped straight after a refusal
    real_read_d142.py CORE flush              off Windows, a real pty and a real shell: a half-typed PIN at a stop

The last two print one line of JSON."""

import importlib.util
import json
import os
import shlex
import sys
import threading
import time
import types


def load(core):
    pkg = types.ModuleType("d142wizard")
    pkg.__path__ = [os.path.join(core, "puffpai_core", "wizard")]
    sys.modules["d142wizard"] = pkg
    path = os.path.join(pkg.__path__[0], "terminal.py")
    spec = importlib.util.spec_from_file_location("d142wizard.terminal", path)
    term = importlib.util.module_from_spec(spec)
    sys.modules["d142wizard.terminal"] = term
    spec.loader.exec_module(term)
    return term


def _console(term, out_path):
    """Nobody types: only the stop ends this read, as Stop BuildBot does."""
    got = {"stdin_is_console": term.is_console(sys.stdin), "stdout_is_console": term.is_console(sys.stdout),
           "before": term._conin_mode()}
    stop = threading.Event()
    a = term.Answers(stop.is_set)

    def stop_soon():
        time.sleep(1.0)
        got["during"] = term._conin_mode()
        stop.set()
    threading.Thread(target=stop_soon, daemon=True).start()
    try:
        a.hidden("  > ")
    except term.SetupStopped:
        got["left_out"] = a.read_is_out
    real_exit = os._exit

    def exit_and_say(code):
        got["after"], got["code"] = term._conin_mode(), code
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(got, f)
        real_exit(code)
    os._exit = exit_and_say
    term._leave(a, 7)


def _nul(term, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"stdin_isatty": sys.stdin.isatty(), "stdout_isatty": sys.stdout.isatty(),
                   "stdin_is_console": term.is_console(sys.stdin), "stdout_is_console": term.is_console(sys.stdout),
                   "setup_passes_unlock": term.is_console(sys.stdin) and term.is_console(sys.stdout)}, f)


def _reader(fd):
    import select
    box = bytearray()

    def read_for(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    return
                if not data:
                    return
                box.extend(data)
    return box, read_for


def _until_prompt(box, read_for, seconds=15.0):
    end = time.time() + seconds
    while b"  > " not in box and time.time() < end:
        read_for(0.05)


def _gap(core):
    import pty
    import signal
    import termios
    sys.stdout.flush()
    pid, fd = pty.fork()
    if pid == 0:
        term = load(core)

        class Engine:
            def submit(self, step, answers=None, renderer="terminal"):
                return {"status": "working", "title": "Plan", "lines": [], "vault": True, "poll_s": 2.0}
        term.Terminal(Engine(), answers=term.Answers(lambda: False), unlock=lambda p: (False, "REFUSED-LINE")).run_step("plan")
        os._exit(0)
    box, read_for = _reader(fd)
    _until_prompt(box, read_for)
    time.sleep(0.2)
    os.write(fd, b"first-wrong\n")
    # How long echo is on, from getpass ending to the next hidden read. The stand-in route answers at once.
    began, ended = None, None
    end = time.time() + 5
    while time.time() < end and ended is None:
        echo = bool(termios.tcgetattr(fd)[3] & termios.ECHO)
        now = time.perf_counter()
        if echo and began is None:
            began = now
        elif not echo and began is not None:
            ended = now
    read_for(0.1)
    # A second refusal, and she types straight after it, with no look at the terminal first: the fault this finds.
    os.write(fd, b"second-wrong\n")
    end = time.time() + 5
    while box.count(b"REFUSED-LINE") < 2 and time.time() < end:
        read_for(0.02)
    refused = box.count(b"REFUSED-LINE") >= 2
    time.sleep(0.2)
    os.write(fd, b"retyped-pin")
    read_for(3.0)
    text = box.decode(errors="replace")
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    print(json.dumps({"refused": refused, "retyped_visible": "retyped-pin" in text,
                      "first_visible": "first-wrong" in text or "second-wrong" in text,
                      "asked_again": text.count("  > ") >= 3,
                      "echo_on_ms": None if began is None or ended is None else round((ended - began) * 1000, 2)}))


def _stopping_kid(core):
    """Run by the shell in ``flush``: a stop comes while she is at the hidden read, and setup leaves as it really does."""
    term = load(core)
    stop = threading.Event()
    a = term.Answers(stop.is_set)
    threading.Timer(2.0, stop.set).start()
    try:
        a.hidden("  > ")
    except term.SetupStopped:
        pass
    print("Setup stopped.", flush=True)
    term._leave(a, 1)


def _flush(core):
    import pty
    import signal
    sys.stdout.flush()
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(PS1="SHELL$ ", HISTFILE="/dev/null", PYTHONDONTWRITEBYTECODE="1")
        os.execvp("bash", ["bash", "--norc", "--noprofile", "-i"])
    box, read_for = _reader(fd)
    read_for(0.5)
    argv = [sys.executable, os.path.abspath(__file__), core, "stopping-kid"]
    os.write(fd, (" ".join(shlex.quote(x) for x in argv) + "\n").encode())
    _until_prompt(box, read_for)
    time.sleep(0.3)
    os.write(fd, b"half-of-my-pin")            # she is typing her PIN when Stop comes
    read_for(3.0)
    mark = len(box)
    os.write(fd, b"-rest\n")
    read_for(1.0)
    after = bytes(box[mark:]).decode(errors="replace")
    text = box.decode(errors="replace")
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    print(json.dumps({"stopped": "Setup stopped." in text, "half_reached_the_shell": "half-of-my-pin" in text,
                      "shell_echoes_again": "-rest" in after}))


if __name__ == "__main__":
    folder, what = sys.argv[1], sys.argv[2]
    if what == "console":
        _console(load(folder), sys.argv[3])
    elif what == "nul":
        _nul(load(folder), sys.argv[3])
    elif what == "gap":
        _gap(folder)
    elif what == "flush":
        _flush(folder)
    elif what == "stopping-kid":
        _stopping_kid(folder)
    else:
        sys.exit(f"no such scenario: {what}")
