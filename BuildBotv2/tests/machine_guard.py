"""Tests never change this computer (lane I7): no registry writes, and no start of a program that installs things
or edits the registry. Installed by conftest when it is imported, so before any test module.

Why: on 10-22 a verifier run with LOCALAPPDATA pointed at its scratch started the py launcher; the Python install
manager installed a Python into that scratch and registered it in the owner's real HKCU, and that key outlived
the folder.

What it refuses, the way Windows itself reads a program name (so another spelling of the same file is caught):
- winreg writes (CreateKey, SetValueEx, DeleteKey, ...), also when a module took them with ``from winreg import``;
- a start (``subprocess.Popen``, so ``run``, asyncio and ``os.popen`` too; ``os.system``, ``os.startfile``,
  ``os.spawnv``/``spawnve``, ``os.execv``/``execve``) of py, pyw, pymanager, pywmanager, winget, msiexec, reg,
  regedit or regedt32, by name or by the program's own version info (a renamed copy keeps it); of the Store's
  app links (a reparse point) named py*, anything in WindowsApps named py*, or anything in the install
  manager's Python\\bin; and ``os.startfile`` of a .reg or installer file.
- Names are read as Windows does: "/" and "." segments folded, trailing dots and spaces dropped, 8.3 short
  names expanded (GetLongPathNameW), case ignored. A bare name is looked up as Windows would (this program's
  folder, the current folder, System32, Windows) and on BOTH the PATH given to the child and this process's own.
- For a shell (shell=True, os.system, cmd, powershell, pwsh, sh, bash), the whole command string is read: any
  refused name as a word (with or without .exe), each word as a program, any stub path, and a PowerShell
  -EncodedCommand (every spelling: - or /, -e, -ec, any longer prefix) decoded first. Each string is read as
  written and again with cmd's carets, PowerShell's backticks and double quotes taken out.
Not covered: see "Known gaps" in tests/conftest.py (ctypes, multiprocessing, child processes, names a shell
builds at run time, ...).
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import sys

#: winreg calls that change the registry. Reads (OpenKey, QueryValueEx, EnumKey, ...) stay allowed.
WINREG_WRITES = ("CreateKey", "CreateKeyEx", "SetValue", "SetValueEx", "DeleteKey", "DeleteKeyEx",
                 "DeleteValue", "LoadKey", "SaveKey", "DisableReflectionKey", "EnableReflectionKey")
#: Programs a test never starts (file names, ".exe" and the like dropped).
NEVER_START = frozenset({"py", "pyw", "pymanager", "pywmanager", "winget", "msiexec", "reg", "regedit",
                         "regedt32"})
SHELLS = frozenset({"cmd", "powershell", "pwsh", "sh", "bash"})
#: os.startfile of these opens an installer or a registry merge.
NEVER_OPEN = (".reg", ".msi", ".msp", ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller")
_EXTS = (".exe", ".com", ".cmd", ".bat")
_APPEXECLINK = 0x8000001B
_WORD = re.compile(r"(?<![\w.\-])(" + "|".join(sorted(NEVER_START, key=len, reverse=True))
                   + r")(?:\.exe)?[.\s]*(?![\w\-])", re.I)
_STUB_TEXT = re.compile(r"\\windowsapps\\+py|\\python\\+bin\\", re.I)
_SPLIT = re.compile(r"[\s&|;()<>^\"'`,{}]+")
_OPTION = re.compile(r"^[/-][\w:]{1,30}$")

HITS: list[str] = []
_PATCHED: dict = {}


class MachineChangeInTest(AssertionError):
    """A test tried to change this computer (the registry, or an installer). Use a fake instead."""


def _long(path: str) -> str:
    """GetLongPathNameW: 8.3 parts of an existing path written out in full (the path as given otherwise)."""
    if os.name != "nt":
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetLongPathNameW(path, buf, 32768)
    return buf.value if 0 < n < 32768 else path


def canon(word: str, cwd: str | None = None) -> str:
    """A program path the way Windows reads it, lower case: full, backslashes, "." and ".." folded, trailing dots
    and spaces dropped from each part, 8.3 names expanded."""
    p = str(word).strip().strip('"').strip("'")
    if os.name == "nt":
        p = p.replace("/", "\\")
        parts = [x if set(x) <= {"."} else x.rstrip(". ") for x in p.split("\\")]
        p = "\\".join(parts)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    p = os.path.normpath(os.path.abspath(p))
    return _long(p).lower()


def _base(path: str) -> str:
    name = re.sub(r"^[A-Za-z]:", "", re.split(r"[\\/]", path)[-1])     # "C:py.exe" (drive-relative) is py.exe
    name = name.split(":")[0].rstrip(". ").lower()                       # "py.exe::$DATA" is py.exe too
    for ext in _EXTS:
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def _original_name(path: str) -> str:
    """The OriginalFilename in a program's own version info (lower case, ".mui" dropped), or ""."""
    if os.name != "nt" or not os.path.isfile(path):
        return ""
    import ctypes
    from ctypes import wintypes
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(path, None)
    if not size:
        return ""
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(path, 0, size, buf):
        return ""
    ptr, n = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(n)) or not n.value:
        langs = [(0x0409, 0x04B0)]
    else:
        raw = ctypes.string_at(ptr.value, n.value)
        langs = [(int.from_bytes(raw[i:i + 2], "little"), int.from_bytes(raw[i + 2:i + 4], "little"))
                 for i in range(0, len(raw) - 3, 4)]
    for lang, cp in langs:
        key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\OriginalFilename"
        if ver.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(n)) and n.value:
            name = ctypes.wstring_at(ptr.value, n.value).rstrip("\x00").strip().lower()
            return name[:-4] if name.endswith(".mui") else name
    return ""


def _is_app_link(path: str) -> bool:
    try:
        return getattr(os.lstat(path), "st_reparse_tag", 0) == _APPEXECLINK
    except OSError:
        return False


def _why_path(path: str) -> str | None:
    """Why this one canonical program path is refused, or None."""
    name = _base(path)
    if name in NEVER_START:
        return f"{name} ({path})"
    if "\\python\\bin\\" in path:
        return f"an install-manager shortcut ({path})"
    if name.startswith("py") and ("\\windowsapps\\" in path or _is_app_link(path)):
        return f"the Store or install-manager stub {path}"
    orig = _original_name(path)
    if orig:
        # py.exe and pyw.exe are left out here: a venv's own python.exe is built from the same launcher source
        # and says "py.exe" too (it only runs that venv's Python). The install manager's launcher, which can
        # install a Python, says "launcher-64.exe" (or -32, -arm64).
        oname = _base(orig)
        if (oname in NEVER_START and oname not in ("py", "pyw")) or oname.startswith("launcher"):
            return f"{orig}, renamed ({path})"
    return None


def _where(word: str, env, cwd) -> list[str]:
    """Every file a start of *word* could run: as given, or for a bare name each place Windows looks."""
    w = str(word).strip().strip('"').strip("'")
    if not w:
        return []
    if re.search(r"[\\/]", w) or re.match(r"^[A-Za-z]:", w):
        return list(dict.fromkeys([canon(w, os.getcwd()), canon(w, cwd)] if cwd else [canon(w)]))
    name = w.rstrip(". ")
    out = [name.lower()]
    win = os.environ.get("SystemRoot", r"C:\Windows")
    child_path = next((v for k, v in (env or {}).items() if str(k).upper() == "PATH"), None) if env else None
    # (A) what CreateProcess runs: this program's folder, the current folder, System32, Windows, then THIS
    # process's PATH (never the child's). (B) what a shell inside the child would run: its folder, then its PATH.
    orders = ([os.path.dirname(sys.executable), os.getcwd(), os.path.join(win, "System32"), win,
               *os.environ.get("PATH", "").split(os.pathsep)],
              [cwd or os.getcwd(), *(child_path if child_path is not None else os.environ.get("PATH", "")).split(os.pathsep)])
    for dirs in orders:
        hit = shutil.which(name, path=os.pathsep.join(d for d in dirs if d))
        if hit:
            out.append(canon(hit))
    return list(dict.fromkeys(out))


def why_word(word: str, env=None, cwd=None) -> str | None:
    for path in _where(word, env, cwd):
        why = _why_path(path)
        if why:
            return why
    return None


#: PowerShell's -EncodedCommand: "-" or "/", then -e, -ec, or any longer prefix of EncodedCommand; then the value.
_ENCODED = re.compile(r"(?i)(?:^|[\s\"'])[-/](?:ec|e(?:n(?:c(?:o(?:d(?:e(?:d(?:c(?:o(?:m(?:m(?:a(?:n(?:d)?)?)?)?)?)?)?)?)?)?)?)?)?)"
                      r"(?:[:\s]+)[\"']?([A-Za-z0-9+/=]+)")
_POWERSHELL = re.compile(r"(?i)(?:^|[\s\\/\"'&|;(])(?:powershell|pwsh)(?:\.exe)?\.?(?=$|[\s\"'])")


def _unescaped(text: str) -> list[str]:
    """The ways a shell may read *text*: as written; with cmd's carets taken out (``^x`` is ``x``, ``^^`` is
    ``^``); with PowerShell's backticks taken out; and with double quotes taken out (``p"y"`` runs ``py``)."""
    caret = re.sub(r"\^(.)", r"\1", text).rstrip("^")
    tick = re.sub(r"`(.)", r"\1", text).rstrip("`")
    both = re.sub(r"`(.)", r"\1", caret).rstrip("`")
    return list(dict.fromkeys([text, caret, tick, both, both.replace('"', "")]))


def why_shell(command: str, env=None, cwd=None, powershell: bool = False) -> str | None:
    """Why a shell command string is refused, or None: refused names as words, each word as a program, stub
    paths in it, a PowerShell -EncodedCommand inside it (decoded first). Read as written and with cmd's
    carets, PowerShell's backticks and quotes taken out."""
    for text in _unescaped(str(command)):
        m = _WORD.search(text)
        if m:
            return f"{m.group(1).lower()} in a shell command"
        folded = re.sub(r"\\(?:\.\\)+", "\\\\", text.replace("/", "\\"))
        if _STUB_TEXT.search(folded):
            return "a stub path in a shell command"
        words = [w for w in _SPLIT.split(text) if w and not _OPTION.match(w)]
        for w in words:
            why = why_word(w, env, cwd)
            if why:
                return why + " in a shell command"
        if powershell or _POWERSHELL.search(text):
            enc = _ENCODED.search(text)
            if enc:
                try:
                    inner = base64.b64decode(enc.group(1), validate=True).decode("utf-16-le")
                except (ValueError, UnicodeDecodeError):
                    return "a PowerShell -EncodedCommand that can't be read"
                why = why_shell(inner, env, cwd, powershell=True)
                if why:
                    return why
    return None


def why_start(args, executable=None, shell=False, env=None, cwd=None) -> str | None:
    """Why a process start is refused, or None."""
    if isinstance(args, (bytes, bytearray)):
        args = args.decode(errors="replace")
    if isinstance(args, str):
        argv = None
        line = args
    else:
        argv = [a.decode(errors="replace") if isinstance(a, (bytes, bytearray)) else os.fspath(a)
                if hasattr(a, "__fspath__") else str(a) for a in (args or [])]
        line = None
    progs = []
    if executable:
        progs.append(os.fspath(executable) if hasattr(executable, "__fspath__") else str(executable))
    if argv:
        progs.append(argv[0])
    elif line is not None:
        first = line.strip()
        progs.append(first[1:].split('"', 1)[0] if first.startswith('"') else (first.split(None, 1) or [""])[0])
    for p in progs:
        for spelling in _unescaped(p):
            why = why_word(spelling, env, cwd)
            if why:
                return why
    if shell:
        return why_shell(line if line is not None else " ".join(argv or []), env, cwd)
    names = [_base(x) for p in progs for x in _where(p, env, cwd)]
    if any(n in SHELLS for n in names):
        rest_argv = list(argv[1:]) if argv else None
        ps = any(n in ("powershell", "pwsh") for n in names)
        if rest_argv is not None and ps:
            # -File runs that script; what follows is the script's own arguments, not a command to read.
            at = next((i for i, a in enumerate(rest_argv) if re.fullmatch(r"(?i)-f(?:i(?:l(?:e)?)?)?", a)), None)
            if at is not None:
                why = why_word(rest_argv[at + 1], env, cwd) if at + 1 < len(rest_argv) else None
                return why or why_shell(" ".join(rest_argv[:at]), env, cwd, powershell=True)
        rest = " ".join(rest_argv) if rest_argv is not None else line
        return why_shell(rest, env, cwd, powershell=ps)
    return None


def _refuse(why: str):
    HITS.append(why)
    raise MachineChangeInTest(f"a test started {why}: tests never run the py launcher, pymanager, an installer "
                              "or the registry editor (conftest guard).")


def install() -> None:
    """Patch once, for the whole session (conftest calls it when it is imported)."""
    if _PATCHED:
        return
    import inspect
    import subprocess
    try:
        import winreg
    except ImportError:                                   # not Windows: no registry to protect
        winreg = None
    if winreg is not None:
        for name in WINREG_WRITES:
            real = getattr(winreg, name, None)
            if real is None:
                continue

            def refuse(*_a, _name=name, **_k):
                HITS.append(f"winreg.{_name}")
                raise MachineChangeInTest(f"a test called winreg.{_name}: tests never write the registry "
                                          "(conftest guard). Use a fake.")
            _PATCHED[("winreg", name)] = (winreg, real, refuse)
            setattr(winreg, name, refuse)
    real_init = subprocess.Popen.__init__
    sig = inspect.signature(real_init)

    def guarded_init(self, *a, **k):
        try:
            got = sig.bind(self, *a, **k).arguments
        except TypeError:
            return real_init(self, *a, **k)
        why = why_start(got.get("args"), got.get("executable"), got.get("shell", False), got.get("env"),
                        got.get("cwd"))
        if why:
            _refuse(why)
        return real_init(self, *a, **k)
    _PATCHED[("Popen", "__init__")] = (subprocess.Popen, real_init, guarded_init)
    subprocess.Popen.__init__ = guarded_init

    real_system = os.system

    def system(command):
        why = why_start(command, shell=True)
        if why:
            _refuse(why)
        return real_system(command)
    _PATCHED[("os", "system")] = (os, real_system, system)
    os.system = system

    if hasattr(os, "startfile"):
        real_startfile = os.startfile

        def startfile(path, *a, **k):
            target = os.fspath(path) if hasattr(path, "__fspath__") else str(path)
            if canon(target).endswith(NEVER_OPEN):
                _refuse(f"an installer or registry file ({target})")
            why = why_word(target, None, k.get("cwd"))
            more = k.get("arguments", a[1] if len(a) > 1 else "")
            why = why or (why_shell(more) if more else None)
            if why:
                _refuse(why)
            return real_startfile(path, *a, **k)
        _PATCHED[("os", "startfile")] = (os, real_startfile, startfile)
        os.startfile = startfile

    for name in ("spawnv", "spawnve", "execv", "execve"):
        real = getattr(os, name, None)
        if real is None:
            continue

        def spawn(*a, _real=real, _name=name, **k):
            path = a[1] if _name.startswith("spawn") else a[0]
            argv = a[2] if _name.startswith("spawn") else a[1]
            why = why_word(os.fspath(path) if hasattr(path, "__fspath__") else str(path)) or \
                why_start(list(argv), shell=False)
            if why:
                _refuse(why)
            return _real(*a, **k)
        _PATCHED[("os", name)] = (os, real, spawn)
        setattr(os, name, spawn)


def sweep() -> list[str]:
    """A module that took a winreg write with ``from winreg import ...`` before :func:`install` holds the real
    one: rebind every such name to the guarded one. Returns where they were."""
    fixed = []
    real_to_guard = {id(real): guard for (mod, name), (_m, real, guard) in _PATCHED.items() if mod == "winreg"}
    for modname, mod in list(sys.modules.items()):
        if mod is None or modname == "winreg":
            continue
        try:
            items = list(vars(mod).items())
        except TypeError:
            continue
        for attr, val in items:
            guard = real_to_guard.get(id(val))
            if guard is not None:
                setattr(mod, attr, guard)
                fixed.append(f"{modname}.{attr}")
    return fixed


def uninstall() -> None:
    for (_mod, name), (owner, real, _guard) in _PATCHED.items():
        setattr(owner, name, real)
    _PATCHED.clear()
