"""Floor: nothing in BuildBot or its tests ends processes broadly (by tree, by name or pattern, by group).

Rule (orchestrator, 10-22): kill only processes you started, by a handle or a Job Object you hold. Windows
reuses PID numbers, so a tree walk over parent PIDs, a name or a pattern can end someone else's program.

What counts as a broad kill, in ``core/``, ``tests/``, ``packages/`` and ``install.ps1``:
- ``taskkill`` with ``/T`` (tree), ``/IM`` (by name) or ``/FI`` (a filter, e.g. ``IMAGENAME eq x``);
- ``pkill``, ``killall``;
- ``Stop-Process`` (or ``spps``, or PowerShell's ``kill``) by ``-Name`` or ``-ProcessName``, or fed a pipeline
  (``Get-Process x | Stop-Process``);
- ``wmic ... delete`` or ``wmic ... call terminate``;
- ``os.killpg``, and ``os.kill`` of a negative number (a process group);
- a psutil-style ``.children(`` walk, or a ``process_iter()`` scan, in code that also kills or terminates.

How it is read:
- Scripts (``.ps1``, ``.cmd``, ``.bat``, ``.sh``): every line is a command line.
- Python: parsed with ``ast``, so an argument list split over many lines is one call. A call that starts a
  program (``subprocess.*``, ``os.system`` / ``os.popen`` / ``os.spawn*``, asyncio's subprocess calls, or any
  call whose first argument is a literal argument list, like ``jobs.Job.spawn([...])``) is checked as that
  program with those arguments. When the program is a shell (``cmd /c``, ``powershell -Command``, ``sh -c``,
  ...) or ``shell=True`` is given, its one-string command is checked like a script line: a shell plus a kill
  string is a real call. A kill line written as a string anywhere else (handed to Toolsmith's deny check, in
  a test that proves it is refused) is data and is not matched.
- JavaScript: every line, as a script line.
The patterns are built from pieces, so this file never matches itself; its own path is skipped too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TK = "task" + "kill"
SP = "Stop-" + "Process"
SCRIPTS = {".ps1", ".cmd", ".bat", ".sh", ".js"}
SHELLS = {"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "dash"}
#: Programs that end other programs: a literal argument list naming one is a real start, whatever runs it
#: (``jobs.Job.spawn([...])``, a helper of the test's own).
KILLERS = {TK, "p" + "kill", "kill" + "all", "wm" + "ic", "tskill", "pskill"}

#: Exact lines allowed, with why. Keep it short; each one is a real case.
ALLOWED = {
    ("core/puffpai_core/steward/jobs.py", "os." + "killpg(self.pid, signal.SIGKILL)"):
        "POSIX only: the group is the package's own new session (start_new_session=True), started by this "
        "Job; it can't name another program's group.",
}

_F = r"(?:^|\s)[/-]{1,2}"
LINE_RULES = [
    ("taskkill by tree, name or filter", re.compile(r"\b" + TK + r"(?:\.exe)?\b.*" + _F + r"(T|IM|FI)\b", re.I)),
    ("pkill or killall", re.compile(r"(?:^|[\s;&|(\"'`])(p" + "kill|kill" + r"all)(?:\.exe)?\b", re.I)),
    ("Stop-Process by name", re.compile(r"\b(" + SP + r"|spps|kill)\b.*\s-(Name|ProcessName|n)\b", re.I)),
    ("a pipeline into Stop-Process", re.compile(r"\|\s*(" + SP + r"|spps)\b", re.I)),
    ("wmic delete or terminate", re.compile(r"\bwm" + r"ic\b.*\b(delete|terminate)\b", re.I)),
]


def line_hits(line: str) -> list[str]:
    """The broad kills in one command line (a script line, or the string a shell is given)."""
    return [name for name, rule in LINE_RULES if rule.search(line)]


def _text(node: ast.AST) -> str | None:
    """A literal string, or an f-string with its holes as ``{}``; None for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
                       for v in node.values)
    return None


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _dotted(node.value) + "." + node.attr
    return ""


_RUNNERS = re.compile(r"(^|\.)(subprocess\.(run|Popen|call|check_call|check_output)|os\.(system|popen|spawn\w*|exec\w*)|"
                      r"create_subprocess_(exec|shell)|Popen)$")


def _program(word: str) -> str:
    name = re.split(r"[\\/]", word.strip().strip('"'))[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _argv_hits(argv: list[str | None]) -> list[str]:
    """Hits for a program and its arguments (None = an argument that is not a literal)."""
    words = [w for w in argv if w is not None]
    if not argv or argv[0] is None:
        return []
    prog = _program(argv[0])
    rest = [w for w in argv[1:] if w is not None]
    if prog in SHELLS:
        return line_hits(" ".join(rest))                 # a shell runs its one-string command
    return line_hits(" ".join([prog] + rest)) if prog else line_hits(" ".join(words))


def python_hits(source: str) -> list[tuple[int, str]]:
    """``[(line, what)]`` for one Python source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name.endswith("os." + "killpg") or name == "kill" + "pg":
            out.append((node.lineno, "os.killpg"))
        if name.endswith("os.kill") and node.args:
            first = node.args[0]
            neg = isinstance(first, ast.UnaryOp) and isinstance(first.op, ast.USub)
            if neg or (isinstance(first, ast.Constant) and isinstance(first.value, int) and first.value < 0):
                out.append((node.lineno, "os.kill of a process group"))
        first = node.args[0] if node.args else None
        shell = any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                    for k in node.keywords)
        argv = None
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts and _text(first.elts[0]) is not None:
            argv = [_text(e) if _text(e) is not None else (_dotted(e) or None) for e in first.elts]
            # a variable holding the program (``tk = ...taskkill.exe``) is read by its name
            argv = [TK if (a is not None and a.lower() in ("tk", "taskkill_exe")) else a for a in argv]
        elif isinstance(first, (ast.List, ast.Tuple)) and first.elts and isinstance(first.elts[0], ast.Name):
            argv = [first.elts[0].id] + [_text(e) for e in first.elts[1:]]
            argv = [TK if (a is not None and a.lower() in ("tk", "taskkill_exe")) else a for a in argv]
            if _program(argv[0]) != TK:
                argv = None
        if argv is not None and (_program(argv[0] or "") in KILLERS | SHELLS or _RUNNERS.search(name)):
            what = _argv_hits(argv)
        elif _RUNNERS.search(name) and first is not None and _text(first) is not None and (
                shell or name.split(".")[-1] in ("system", "popen", "create_subprocess_shell")):
            what = line_hits(_text(first))
        elif _RUNNERS.search(name) and first is not None and _text(first) is not None:
            what = line_hits(_text(first))           # a one-string command to a process call
        else:
            what = []
        out += [(node.lineno, w) for w in what]
    # walks that kill: .children( or process_iter( in the same function as .kill( / .terminate(
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module))]:
        body = fn.body if isinstance(fn, ast.Module) else [fn]
        calls = [c for b in body for c in ast.walk(b) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        walks = [c for c in calls if c.func.attr in ("child" + "ren", "process_" + "iter")]
        kills = [c for c in calls if c.func.attr in ("kill", "terminate", "send_signal")]
        if walks and kills and (isinstance(fn, ast.Module) is False or not any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in fn.body)):
            out += [(w.lineno, "a process walk that kills") for w in walks]
    return sorted(set(out))


def _files():
    me = Path(__file__).resolve()
    for top in ("core", "tests", "packages"):
        for p in (ROOT / top).rglob("*"):
            if p.is_file() and p.resolve() != me and (p.suffix in SCRIPTS or p.suffix == ".py") \
                    and "node_modules" not in p.parts and "__pycache__" not in p.parts:
                yield p
    if (ROOT / "install.ps1").is_file():
        yield ROOT / "install.ps1"


def scan(root: Path | None = None) -> list[str]:
    root = root or ROOT
    found = []
    files = _files() if root == ROOT else [p for top in ("core", "tests", "packages") for p in (root / top).rglob("*")
                                          if p.is_file() and (p.suffix in SCRIPTS or p.suffix == ".py")
                                          and "__pycache__" not in p.parts and p.name != Path(__file__).name] + \
        ([root / "install.ps1"] if (root / "install.ps1").is_file() else [])
    for p in files:
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        if p.suffix == ".py":
            hits = python_hits(text)
        else:
            hits = [(n, w) for n, line in enumerate(lines, 1) for w in line_hits(line)]
        for n, what in hits:
            line = lines[n - 1].strip() if 0 < n <= len(lines) else ""
            if (rel, line) not in ALLOWED:
                found.append(f"{rel}:{n}: {what}: {line}")
    return found


def test_nothing_kills_by_tree_name_pattern_or_group():
    hits = scan()
    assert not hits, "Broad kills found (kill only what you started, by handle or job):\n" + "\n".join(hits)


def test_every_allowed_line_is_still_there():
    """An allowed line that is gone is dropped from the list, so the list never grows stale."""
    for (rel, line), why in ALLOWED.items():
        assert why
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert line in [x.strip() for x in text.splitlines()], f"{rel}: allowed line no longer there: {line}"


#: Python that really runs a broad kill: each one is caught (the verifier's forms included).
PY_RUNS = [
    'subprocess.run(["' + TK + '", "/T", "/F", "/PID", str(pid)])',
    'subprocess.run([tk, "/F", "/T", "/PID", str(pid)])',
    'subprocess.run(["' + TK + '", "/IM", "node.exe", "/F"])',
    'subprocess.run(["' + TK + '", "/F", "/FI", "IMAGENAME eq node.exe"])',
    'subprocess.run([\n    "' + TK + '",\n    "/F",\n    "/IM",\n    "node.exe",\n])',
    'subprocess.run(["powershell", "-Command", "' + SP + ' -Name node"])',
    'subprocess.run(["cmd", "/c", "' + TK + ' /F /IM node.exe"])',
    'subprocess.Popen(["pwsh", "-c", "Get-Process node | ' + SP + '"])',
    'subprocess.run(["p' + 'kill", "node"])',
    'subprocess.run(["kill' + 'all", "node"])',
    'subprocess.run(["wm' + 'ic", "process", "where", "name=\'x.exe\'", "delete"])',
    'subprocess.run(["wm' + 'ic", "process", "where", "name=\'x.exe\'", "call", "terminate"])',
    'subprocess.run("' + TK + ' /F /T /PID 4", shell=True)',
    'os.system("' + TK + ' /F /IM node.exe")',
    'jobs.Job.spawn(["' + TK + '", "/IM", "x.exe"], cwd, env, None)',
    "os.kill" + "pg(pid, 9)",
    "os.kill(-pgid, 9)",
    "for c in p.child" + "ren(recursive=True):\n    c.kill()",
    "kids = p.child" + "ren()\nfor c in kids:\n    c.terminate()",
    "for p in psutil.process_" + "iter(['name']):\n    if p.info['name'] == 'node.exe':\n        p.kill()",
]

#: Script lines that are broad kills (.ps1 / .cmd / .sh).
SCRIPT_RUNS = [
    TK + " /F /T /PID 4", TK + " //F //IM node.exe", TK + ' /FI "IMAGENAME eq node.exe" /F',
    SP + " -Name node", SP + " -ProcessName node -Force", "spps -Name node", "kill -Name node",
    "Get-Process node | " + SP, "Get-Process node | spps", "wm" + "ic process where name='node.exe' call terminate",
    "wm" + "ic process where name='node.exe' delete", "kill" + "all node", "p" + "kill -f server.js",
]

#: Data: kill lines that go to Toolsmith's deny check (never run), and plain code.
PY_DATA = [
    'BY_NAME = ["' + TK + ' /F /IM node.exe", "' + SP + ' -Name node"]',
    'link.try_run(try_id, "' + TK + ' /F /IM node.exe")',
    'tz.check("Get-Process node | ' + SP + '", folder=x)',
    'attempts = [{"program": "' + TK + '", "args": ["/F", "/PID", str(pid)]}]',
    'subprocess.run(["' + TK + '", "/F", "/PID", str(pid)])',      # one process, its own number
    "proc.kill()",
    "os.kill(pid, signal.SIGKILL)",
]


@pytest.mark.parametrize("source", PY_RUNS)
def test_the_rules_catch_python_that_runs_a_broad_kill(source):
    assert python_hits(source), source


@pytest.mark.parametrize("line", SCRIPT_RUNS)
def test_the_rules_catch_script_lines(line):
    assert line_hits(line), line


@pytest.mark.parametrize("source", PY_DATA)
def test_deny_check_data_and_single_kills_are_not_matched(source):
    assert not python_hits(source), source


def test_a_hit_names_its_file_and_line(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "bad.py").write_text('import subprocess\n\nsubprocess.run(["p' + 'kill", "x"])\n',
                                              encoding="utf-8")
    (tmp_path / "install.ps1").write_text("Write-Host hi\n" + SP + " -Name x\n", encoding="utf-8")
    hits = scan(tmp_path)
    assert any(h.startswith("core/bad.py:3: ") for h in hits), hits
    assert any(h.startswith("install.ps1:2: ") for h in hits), hits


def test_the_page_reader_finds_no_process_by_its_command_line():
    """The page reader's browser is ended with its own job (coordinator decision): no process list, no
    command-line match, no kill program. It is inside ``core/``, so the rules above cover it too."""
    rel = "core/puffpai_core/zone2/page_reader.py"
    assert any(p.relative_to(ROOT).as_posix() == rel for p in _files())
    text = (ROOT / rel).read_text(encoding="utf-8")
    for word in (TK, "Win32_" + "Process", "/bin/" + "ps", "os.kill(", "_profile_" + "pids"):
        assert word not in text, f"{rel}: {word}"
