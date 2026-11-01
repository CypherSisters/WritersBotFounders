"""Floor: nothing in BuildBot or its tests writes the Windows registry (lane I7).

Why: on 10-22 a test-side run left a real key in the owner's HKCU\\Software\\Python\\PythonCore pointing at a
scratch folder that was later deleted. BuildBot reads the registry (the Python finder, the CPU name, the default
browser) and never changes it; changing it is a step the owner takes herself.

What counts as a registry write, in ``core/``, ``tests/``, ``packages/`` and ``install.ps1``:
- ``reg add``, ``reg delete``, ``reg import``, ``reg copy``, ``reg restore``, ``reg load``, ``reg unload``, ``regedit``;
- PowerShell item commands (``New-ItemProperty``, ``Set-ItemProperty``, ``Remove-Item``, ``New-Item``, ...) on a
  registry place (``HKCU:``, ``HKLM:``, ``Registry::``, ``HKEY_...``);
- .NET registry writes in scripts (``.CreateSubKey(``, ``.SetValue(``, ``.DeleteSubKey(``, ``.DeleteValue(``, ...);
- Python ``winreg`` write calls (``CreateKey``, ``SetValueEx``, ``DeleteKey``, ``SaveKey``, ...), by any import name
  (``import winreg as w``, ``from winreg import ...``, ``__import__('winreg')``), and the from-import itself;
- Python program starts through any alias (``import subprocess as sp``, ``from subprocess import run``), with
  ``args=``, ``asyncio.create_subprocess_exec`` / ``_shell``, ``os.spawn*`` / ``os.exec*``, and ``os.startfile`` of a
  .reg, .msi or .msix file;
- PowerShell aliases (``sp``, ``ni``, ``rp``, ``si``, ``clp``, ...) on a registry place; Node ``spawn`` / ``execFile``
  (and the Sync forms) of reg or regedit, backtick strings included.

How it is read (the same way as test_no_broad_kills.py):
- Scripts (``.ps1``, ``.cmd``, ``.bat``, ``.sh``): every line is a command line.
- Python: parsed with ``ast``. A ``winreg`` write is a call to one of those names through ``winreg`` (or an alias
  of it, or a name imported from it). A call that starts a program with a literal argument list or a one-string
  command is checked as that command line; for a shell, its command string. A registry line written as a
  string anywhere else (deny-check data, a test that proves it is refused) is data and is not matched.
The patterns are built from pieces, so this file never matches itself; its own path is skipped too.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = {".ps1", ".psm1", ".cmd", ".bat", ".sh", ".js"}
SHELLS = {"cmd", "powershell", "pwsh", "sh", "bash"}
R = "re" + "g"
HIVE = r"(?:HK(?:CU|LM|CR|U|CC):|Registry::|HKEY_[A-Z_]+)"
WINREG_WRITES = {"Create" + "Key", "Create" + "KeyEx", "Set" + "Value", "Set" + "ValueEx", "Delete" + "Key",
                 "Delete" + "KeyEx", "Delete" + "Value", "Load" + "Key", "Save" + "Key", "Enable" + "ReflectionKey",
                 "Disable" + "ReflectionKey"}
ITEM_CMDS = "|".join(v + "-" + n for v in ("New", "Set", "Remove", "Clear", "Rename", "Move", "Copy")
                     for n in ("Item", "ItemProperty"))

#: Exact lines allowed, with why. Empty: nothing in BuildBot writes the registry.
ALLOWED: dict[tuple[str, str], str] = {}

#: A reg or regedit program word: bare, quoted, with a folder in front, ".exe", a trailing dot.
_REG_WORD = r"(?:^|[\s;&|(\"'`\\/=])" + R + r"(?:ed(?:it|t32))?(?:\.exe)?\.?[\"']?"
LINE_RULES = [
    ("reg.exe changes the registry",
     re.compile(r"(?:^|[\s;&|(\"'`\\/])" + R + r"(?:\.exe)?\.?[\"']?\s+(add|delete|import|copy|restore|load|unload)\b",
                re.I)),
    ("Start-Process of reg or regedit", re.compile(r"\bStart-Process\b.*" + _REG_WORD + r"(?:\s|$|[,;)])", re.I)),
    ("& reg (the call operator)",
     re.compile(r"&\s*[\"']?(?:[^\s\"']*[\\/])?" + R + r"(?:ed(?:it|t32))?(?:\.exe)?\.?[\"']?(?:\s|$)", re.I)),
    ("a PowerShell item-property command (any path)",
     re.compile(r"\b(?:Set|New|Remove|Clear|Rename|Copy|Move)-ItemProperty\b", re.I)),
    ("regedit", re.compile(r"(?:^|[\s;&|(\"'`\\/])" + R + r"ed(?:it|t32)(?:\.exe)?\b", re.I)),
    ("a PowerShell item command on the registry", re.compile(r"\b(" + ITEM_CMDS + r")\b.*" + HIVE, re.I)),
    ("a PowerShell alias for an item command on the registry",
     re.compile(r"(?:^|[\s;|&({])(?:sp|ni|rp|si|clp|cli|ri|rm|del|erase|rd|rmdir|cpp|cpi|copy|cp|mi|mv|move|mp|rni|"
                r"ren|rnp)\s.*" + HIVE, re.I)),
    ("a Node child process of reg or regedit",
     re.compile(r"\b(?:spawn|spawnSync|execFile|execFileSync|exec|execSync|fork)\s*\(\s*[\"'`](?:[^\"'`]*[\\/])?"
                + R + r"(?:ed(?:it|t32))?(?:\.exe)?\.?[\"'`\s]", re.I)),
    (".NET registry write", re.compile(r"\.(Create" + "SubKey|Delete" + "SubKey(Tree)?|Delete" + "Value|Set"
                                       + r"Value|Set" + r"AccessControl)\s*\(", re.I)),
]


def line_hits(line: str, js: bool = False) -> list[str]:
    """The registry writes in one command line. JavaScript gets the program rules only (``.SetValue(`` is common
    there and is no registry call)."""
    return [name for name, rule in LINE_RULES if rule.search(line) and not (js and name.startswith(".NET"))]


def _text(node: ast.AST) -> str | None:
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


_RUNNERS = re.compile(r"(^|\.)(subprocess\.(run|Popen|call|check_call|check_output)|os\.(system|popen|spawn\w*|"
                      r"exec\w*)|create_subprocess_(exec|shell)|Popen)$")


def _program(word: str) -> str:
    name = re.split(r"[\\/]", word.strip().strip('"'))[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


WINREG_MODS = ("win" + "reg", "_win" + "reg")
#: Calls that run one program with an argument list (first argument, or ``args=``).
_ARGV_CALLS = {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
               "subprocess.check_output"}
#: Calls that run one command string through a shell.
_SHELL_CALLS = {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput",
                "asyncio.create_subprocess_shell"}
#: os.startfile of these opens an installer or a registry merge.
_OPEN_EXTS = ("." + R, ".msi", ".msp", ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller")


def _import_target(node: ast.AST) -> str | None:
    """``__import__('x')`` or ``importlib.import_module('x')``: the module name ``x``."""
    if isinstance(node, ast.Call) and node.args and _text(node.args[0]) is not None and \
            _dotted(node.func) in ("__import__", "importlib.import_module", "import_module"):
        return _text(node.args[0])
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Every local name for a module or function we care about: ``import subprocess as sp`` (sp -> subprocess),
    ``from subprocess import run as r`` (r -> subprocess.run), ``w = __import__('winreg')`` (w -> winreg)."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                if a.name != "*":
                    names[a.asname or a.name] = node.module + "." + a.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            mod = _import_target(node.value)
            if mod:
                names[node.targets[0].id] = mod
    return names


def _canon(func: ast.AST, names: dict[str, str]) -> str:
    """The dotted name a call really reaches, aliases followed (``sp.run`` -> ``subprocess.run``)."""
    if isinstance(func, ast.Attribute):
        mod = _import_target(func.value)
        if mod:
            return mod + "." + func.attr
        inner = _canon(func.value, names)
        return (inner + "." + func.attr) if inner else ""
    if isinstance(func, ast.Name):
        return names.get(func.id, func.id)
    return ""


def _argv_line(elts) -> str | None:
    argv = [_text(e) for e in elts]
    if not argv or argv[0] is None:
        return None
    words = [w for w in argv if w is not None]
    if _program(argv[0]) in SHELLS:
        return " ".join(w for w in words[1:] if not w.startswith(("/", "-")) or " " in w)
    return " ".join([_program(argv[0])] + words[1:])


def python_hits(source: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names = _aliases(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in WINREG_MODS:
            for a in node.names:
                if a.name in WINREG_WRITES or a.name == "*":
                    out.append((node.lineno, "from winreg import " + a.name))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _canon(node.func, names)
        head, _, last = name.rpartition(".")
        if head in WINREG_MODS and last in WINREG_WRITES:
            out.append((node.lineno, "a winreg write: " + name))
            continue
        if name == "getattr" and len(node.args) >= 2 and _canon(node.args[0], names) in WINREG_MODS \
                and _text(node.args[1]) in WINREG_WRITES:
            out.append((node.lineno, "a winreg write: getattr " + str(_text(node.args[1]))))
            continue
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        shell = isinstance(kw.get("shell"), ast.Constant) and kw["shell"].value is True
        line = None
        if name == "os.startfile":
            target = _text(node.args[0]) if node.args else _text(kw.get("filepath") or kw.get("path") or ast.Constant(0))
            if target and target.lower().rstrip(". ").endswith(_OPEN_EXTS):
                out.append((node.lineno, "os.startfile of an installer or registry file"))
            continue
        if name == "asyncio.create_subprocess_exec" or last == "create_subprocess_exec":
            line = _argv_line(node.args)
        elif re.fullmatch(r"os\.spawn[lv]p?e?", name):
            line = _argv_line(node.args[1:2] + (list(node.args[2].elts[1:]) if len(node.args) > 2 and isinstance(
                node.args[2], (ast.List, ast.Tuple)) and name[8] == "v" else node.args[3:]))
        elif re.fullmatch(r"os\.exec[lv]p?e?", name):
            line = _argv_line(node.args[0:1] + (list(node.args[1].elts[1:]) if len(node.args) > 1 and isinstance(
                node.args[1], (ast.List, ast.Tuple)) and name[7] == "v" else node.args[2:]))
        elif name in _ARGV_CALLS or name in _SHELL_CALLS or _RUNNERS.search(name):
            first = node.args[0] if node.args else kw.get("args")
            if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
                line = _argv_line(first.elts)
                if line is not None and shell:
                    line = " ".join(w for w in (_text(e) for e in first.elts) if w)
            elif first is not None and _text(first) is not None:
                line = _text(first)
        if line:
            out += [(node.lineno, w) for w in line_hits(line)]
    return sorted(set(out))


def _files(root: Path):
    me = Path(__file__).resolve()
    for top in ("core", "tests", "packages"):
        for p in (root / top).rglob("*"):
            if p.is_file() and p.resolve() != me and (p.suffix in SCRIPTS or p.suffix == ".py") \
                    and "node_modules" not in p.parts and "__pycache__" not in p.parts:
                yield p
    if (root / "install.ps1").is_file():
        yield root / "install.ps1"


def scan(root: Path | None = None) -> list[str]:
    root = root or ROOT
    found = []
    for p in _files(root):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        if p.suffix == ".py":
            hits = python_hits(text)
        else:
            js = p.suffix == ".js"
            hits = [(n, w) for n, line in enumerate(lines, 1)
                    if not line.lstrip().startswith(("#", "rem ", "REM ", "::", "//")) for w in line_hits(line, js)]
        for n, what in hits:
            line = lines[n - 1].strip() if 0 < n <= len(lines) else ""
            if (rel, line) not in ALLOWED:
                found.append(f"{rel}:{n}: {what}: {line}")
    return found


def test_nothing_writes_the_registry():
    hits = scan()
    assert not hits, "Registry writes found (BuildBot and its tests only read the registry):\n" + "\n".join(hits)


def test_the_allowlist_stays_empty_or_current():
    for (rel, line), why in ALLOWED.items():
        assert why
        assert line in [x.strip() for x in (ROOT / rel).read_text(encoding="utf-8").splitlines()], rel


#: Python that really writes the registry: each one is caught.
PY_WRITES = [
    "import win" + "reg\nwin" + "reg.Set" + "ValueEx(k, 'a', 0, 1, 'b')",
    "import win" + "reg as wr\nwr.Create" + "KeyEx(wr.HKEY_CURRENT_USER, 'Software\\\\X')",
    "from win" + "reg import Delete" + "Key\nDelete" + "Key(h, 'X')",
    "from win" + "reg import Delete" + "Value as dv\ndv(h, 'X')",
    "import win" + "reg\nwin" + "reg.Delete" + "KeyEx(h, 'X', 0, 0)",
    "import win" + "reg\nwin" + "reg.Set" + "Value(h, 'X', 1, 'v')",
    "import win" + "reg\ngetattr(win" + "reg, 'Delete" + "Value')(h, 'X')",
    "from win" + "reg import Set" + "ValueEx",
    "from win" + "reg import *",
    "from win" + "reg import (OpenKey,\n    Create" + "KeyEx)",
    'subprocess.run(["' + R + '", "add", "HKCU\\\\Software\\\\X", "/v", "a", "/d", "1", "/f"])',
    'subprocess.run(["C:\\\\Windows\\\\System32\\\\' + R + '.exe", "delete", "HKCU\\\\X", "/f"])',
    'subprocess.run(["' + R + 'edit", "/s", "x.' + R + '"])',
    'subprocess.run(["powershell", "-Command", "Set-ItemProperty HKCU:\\\\Software\\\\X -Name a -Value 1"])',
    'subprocess.run(["cmd", "/c", "' + R + ' import settings.' + R + '"])',
    'subprocess.run("' + R + ' add HKCU\\\\X /f", shell=True)',
    'os.system("' + R + ' delete HKLM\\\\Software\\\\X /f")',
    # aliases, keywords and the other ways to start a program (lane I7, last round)
    'import subprocess as sp\nsp.run(["' + R + '", "add", "HKCU\\\\X", "/f"])',
    'from subprocess import run, check_call, Popen\nrun(["' + R + '", "delete", "HKCU\\\\X", "/f"])',
    'from subprocess import check_call\ncheck_call(["' + R + '.exe", "import", "x.' + R + '"])',
    'from subprocess import Popen as P\nP(["' + R + 'edit", "/s", "x"])',
    'import subprocess\nsubprocess.run(args=["' + R + '", "add", "HKCU\\\\X"])',
    'import subprocess as sp\nsp.Popen(args="' + R + ' add HKCU\\\\X /f", shell=True)',
    'import asyncio\nasyncio.create_subprocess_exec("' + R + '", "add", "HKCU\\\\X")',
    'from asyncio import create_subprocess_shell as cs\ncs("' + R + ' delete HKCU\\\\X /f")',
    'import os\nos.spawnv(os.P_WAIT, "C:\\\\Windows\\\\' + R + '.exe", ["' + R + '", "add", "HKCU\\\\X"])',
    'import os\nos.spawnl(os.P_WAIT, "' + R + '.exe", "' + R + '", "delete", "HKCU\\\\X")',
    'import os\nos.execv("' + R + '", ["' + R + '", "import", "x"])',
    'from os import system as s\ns("' + R + ' add HKCU\\\\X")',
    "import win" + "reg\nwin" + "reg.Save" + "Key(h, 'x.hiv')",
    "w = __import__('win" + "reg')\nw.Set" + "ValueEx(h, 'a', 0, 1, 'b')",
    "__import__('win" + "reg').Delete" + "Key(h, 'X')",
    "import importlib\nw = importlib.import_module('win" + "reg')\nw.Create" + "Key(h, 'X')",
    "import os\nos.startfile('settings." + R + "')",
    "import os\nos.startfile(r'C:\\\\x\\\\setup.msi')",
    "from os import startfile\nstartfile('app.msix')",
]

SCRIPT_WRITES = [
    R + " add HKCU\\Software\\X /v a /d 1 /f", R + ".exe delete HKLM\\Software\\X /f", R + " import x." + R,
    "New-ItemProperty -Path HKCU:\\Software\\X -Name a -Value 1", "Set-ItemProperty HKLM:\\X -Name a -Value 1",
    "Remove-Item -Path Registry::HKEY_CURRENT_USER\\X -Recurse", "New-Item -Path HKCU:\\Software\\X -Force",
    "Remove-ItemProperty -Path HKCU:\\X -Name a", R + "edit /s x." + R,
    "[Microsoft.Win32.Registry]::CurrentUser.Create" + "SubKey('Software\\X')",
    "$k.Set" + "Value('a', 1)", "$k.Delete" + "SubKeyTree('X')",
    "Start-Process " + R + " -ArgumentList 'add HKCU\\X /f'", "Start-Process -FilePath " + R + "edit -Wait",
    "Start-Process 'C:\\Windows\\" + R + ".exe' 'import x'", "& " + R + " add HKCU\\X /f", "& '" + R + ".exe' delete X",
    '& "C:\\Windows\\System32\\' + R + '.exe" add X', "& " + R + "edit /s x." + R,
    "Set-ItemProperty -Path $key -Name a -Value 1", "New-ItemProperty $p -Name a -Value 1",
    "Remove-ItemProperty -LiteralPath $where -Name a", R + ". add HKCU\\X", "'" + R + ".exe' add HKCU\\X",
    "sp HKCU:\\Software\\X -Name a -Value 1", "ni HKCU:\\Software\\X", "rp -Path HKLM:\\X -Name a",
    "si -Path HKCU:\\X -Value 1", "clp HKCU:\\X -Name a", "ri HKCU:\\Software\\X -Recurse",
    "x; sp -Path Registry::HKEY_CURRENT_USER\\X -Name a -Value 1",
]

#: JavaScript that starts reg or regedit (backtick strings included).
JS_WRITES = [
    "spawn('" + R + "', ['add', 'HKCU\\\\X'])", 'spawnSync("' + R + '.exe", ["delete", "X"])',
    "execFile('C:\\\\Windows\\\\" + R + ".exe', ['import', 'x'])", "execFileSync(`" + R + "`, ['add'])",
    "exec(`" + R + " add HKCU\\\\X /f`)", "child_process.execSync('" + R + "edit /s x')",
]
JS_FINE = ["spawn('node', ['x.js'])", "execFile('git', ['status'])", "const region = 'x'", "exec(`registry-tool list`)"]


def test_the_rules_catch_javascript_starts_of_reg():
    for line in JS_WRITES:
        assert line_hits(line, js=True), line
    for line in JS_FINE:
        assert not line_hits(line, js=True), line

#: Reads, and registry lines that are data (deny-check strings), are not matched.
PY_DATA = [
    "import win" + "reg\nwith win" + "reg.OpenKey(win" + "reg.HKEY_CURRENT_USER, 'Software') as k:\n"
    "    win" + "reg.QueryValueEx(k, 'x')",
    'BAD = ["' + R + ' add HKCU\\\\X /f", "Set-ItemProperty HKCU:\\\\X -Name a -Value 1"]',
    'link.try_run(try_id, "' + R + ' add HKCU\\\\Software\\\\X /v a /d 1 /f")',
    'subprocess.run(["' + R + '", "query", "HKCU\\\\Software"])',
    "d = {}\nd.setdefault('a', 1)",
    'import subprocess as sp\nsp.run(["' + R + '", "query", "HKCU\\\\X"])',
    "import os\nos.startfile('notes.txt')",
    "from mylib import run\nrun(['" + R + "', 'add', 'x'])",
    "w = __import__('win" + "reg')\nw.QueryValueEx(h, 'a')",
]

SCRIPT_READS = [
    "Get-ItemProperty HKCU:\\Software\\X", "Get-ChildItem -LiteralPath HKCU:\\Software\\Python",
    "$ip = $key.OpenSubKey('InstallPath')", R + " query HKCU\\Software", "Remove-Item -LiteralPath $tmp -Recurse",
    "New-Item -ItemType Directory -Path $BotDir -Force", "Start-Process notepad.exe", "& $Puffpai setup",
    "Start-Process -FilePath $exe -ArgumentList 'region'", "& registry-tool.exe list",
]


def test_the_rules_for_javascript_leave_out_only_the_dotnet_one():
    assert line_hits("el.Set" + "Value(1)", js=True) == []
    assert line_hits("exec('" + R + " add HKCU\\\\X /f')", js=True)
    assert line_hits("el.Set" + "Value(1)")                       # in a script it is a .NET registry write


def test_the_rules_catch_python_writes():
    for src in PY_WRITES:
        assert python_hits(src), src


def test_the_rules_catch_script_writes():
    for line in SCRIPT_WRITES:
        assert line_hits(line), line


def test_reads_and_data_are_not_matched():
    for src in PY_DATA:
        assert not python_hits(src), src
    for line in SCRIPT_READS:
        assert not line_hits(line), line


def test_a_hit_names_its_file_and_line(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "bad.py").write_text("import win" + "reg\n\nwin" + "reg.Set" + "ValueEx(1, 'a', 0, 1, 'b')\n",
                                              encoding="utf-8")
    (tmp_path / "install.ps1").write_text("Write-Host hi\nSet-ItemProperty HKCU:\\X -Name a -Value 1\n",
                                          encoding="utf-8")
    hits = scan(tmp_path)
    assert any(h.startswith("core/bad.py:3: ") for h in hits), hits
    assert any(h.startswith("install.ps1:2: ") for h in hits), hits
