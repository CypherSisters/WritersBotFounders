# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/toolsmith_zone.py (adapted: v2 places, built fail-closed, check())
"""Toolsmith's zone: the places it may touch, and the places it never may.

Owner, 2026-10-14: "Toolsmith shouldn't have access to the internal parts, as
it's the agent that touches the outside." Toolsmith downloads and runs
strangers' code and may talk to a cloud model, so whatever it reads may leave
the computer. It belongs to zone 3.

* **Allowed**: the current try's folder (a ``host_apps/<id>`` folder, or the
  owner's chosen folder of her code), its own scratch folder (exactly
  ``logs/toolsmith/<id>/scratch``, D70; never its server logs), and
  READ-only the toolchains (the folders of node, npm, python, git ...) and
  their package caches.
* **Never** (wins over everything, even her yes for a step outside the app's
  folder): BuildBot's state folder, the vault, the data folder (now and
  before) and linked folders, BuildBot's own code, other apps' folders, and
  her sign-in keys, browser profiles, startup items and personal folders. A
  folder that HOLDS one of them is refused too (``find ~``, ``-v C:\\Users\\x:/x``).
* Everything else is "outside": the old rule (it waits for her yes).

The terminal check here reads the command the way a shell would (``~``,
``$HOME``, ``%APPDATA%``, ``$env:...``, ``..``, quotes, redirections, ``cd``,
copy targets, Docker mounts). It is a boundary for the model's own commands,
not a sandbox: a native program Toolsmith runs still runs as the owner
(AGENTS.md, "Toolsmith's zone"). Since D60 and D61 it is the SECOND check:
the Core never hands a try's command to a shell, and runs only one program
and its arguments from an allowlist, matched by the resolved file
(``toolsmith_host.allowlist``). Text inspection alone can't be complete; the
structure is the boundary, and this check still reads every program line
and path for places outside the try.

v2 changes (09 section 5, X23):
* **Built fail-closed.** v1 dropped a place whose setting could not be read
  (``toolsmith_zone.py:152-156``, ``:165-176`` at ``63c8a037``), so a broken
  getter turned a "never" place into "outside". Here any place that can't be
  read makes the whole zone refuse every path and every command
  (:class:`ZoneUnreadable`, ``Zone.broken``).
* The places are v2's (01 "Where things live"): the bot folder (``registry.db``,
  ``settings``, ``checklist.json``, ``tokens.db``, ``logs``), the vault,
  ``packages`` (other apps), the owner's data folder and linked folders (lane
  L3's ``settings/folders.json``: ``data``, a path, default ``<state>/data``;
  ``linked``, a list of paths; ``pinned``, a list of whole paths to single
  files, D125), and the Core's code. A ``folders.json`` that is there but can't
  be read, or holds anything but those shapes, makes the zone refuse everything.
* Python's start-up hooks are never touched: a ``.pth`` file, ``sitecustomize.py``
  or ``usercustomize.py`` in any ``site-packages`` (or ``dist-packages``) folder
  is denied, read or write, because Python runs them at every start, the
  Core's own Python included. **Any write into any site-packages folder** is
  denied (so ``copy a.pth <try>/.venv/Lib/site-packages/`` is refused whatever
  the file is called), and so is writing anywhere in the Python that runs the
  Core (its ``sys.prefix`` and ``sys.base_prefix``).
* Paths written so the checks can't follow them: an NTFS stream (``q.pth::$DATA``,
  ``a.txt:hidden``) is denied, read or write. A long-path or device prefix
  (two slashes of either kind, then ``?`` or ``.``) or a UNC path is read as what the kernel
  would really open (:func:`_odd_form`), links followed for this computer's own paths only. A write through
  one is denied. A read waits for her Yes at best, never an outright allow. One that can't be mapped
  (``Volume{...}``, ``GLOBALROOT``, ``Device``, an admin share that isn't a drive here) is denied, and so
  is a name with a trailing dot or space, which plain paths can't name. A real share stays text only.
* A write target held in a variable the check can't resolve, and a command that builds a path by joining
  strings (``"a" + "b"``, ``Join-Path``, ``-join``), are denied for writes and wait for her Yes for reads.
* Copy and move: a folder destination means that folder plus each source's
  name, and both are checked as writes.
* :func:`check` is the one call the Core's exec broker and Toolsmith's tools
  make (lane L6): ``allow``, ``deny`` or ``ask`` (outside: waits for her Yes).
* Linux and Mac (D132), the same rules in their own shape. An NTFS stream and the long-path prefixes don't exist
  there, so those words are plain file names (``//home/x`` is ``/home/x``, read as what it opens). Writing into a
  system folder (``/etc``, ``/usr``, ``/Library`` ...) is denied, and so is any use of a device in ``/dev``, as a
  device path is on Windows; a mount of the whole of ``/tmp`` too, since other programs' sign-in sockets are there.
  A path outside the home folder waits for her Yes when it is a write or names something on this computer; one that
  names nothing here is a path inside a container. A Mac's disk ignores letter case, so here the check does too.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ntpath
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

#: The one line the model gets back for a refused path (no real path in it).
REFUSED = "That path is outside what Toolsmith may touch ({category})."

STATE = "BuildBot's own files"
VAULT = "your vault"
DATA = "your data folder"
LINKED = "a folder you linked"
PINNED = "a document you pinned"
CODE = "BuildBot's own code"
OTHER_APP = "another app's folder"
KEYS = "your sign-in keys and passwords"
BROWSER = "your browser profiles"
STARTUP = "your startup items"
PERSONAL = "your personal folders"
WHOLE = "your whole home folder or a whole drive"
ENGINE = "the Docker engine itself, which can reach every folder"
UNREADABLE = "PuffPAI could not read where its own places are, so nothing is allowed until it can"
PYSTART = "Python's start-up files, which run in every Python program"
STREAM = "a hidden part of a file (an NTFS stream)"
UNCHECKABLE = "a place written in a way PuffPAI can't check"
SYSTEM = "a system folder of this computer"

#: A container that holds the Docker engine (its socket or pipe) or runs
#: privileged can mount any folder on this computer: refused like a denied place.
_ENGINE = re.compile(r"(?i)docker\.sock\b|[\\/]{2}\.[\\/]pipe[\\/]docker_engine|"
                     r"--privileged\b|--pid[ =]host\b|--cap-add[ =]\S*sys_admin|"
                     r"^\s*privileged\s*:\s*true\b|^\s*pid\s*:\s*[\"']?host\b", re.M)

#: Categories where a folder she chose for her code may sit INSIDE (only that
#: subfolder is then allowed): her data folder, a linked folder, Documents.
_INSIDE_OK = (DATA, LINKED, PERSONAL)

ALLOW, DENY, OUTSIDE = "allow", "deny", "outside"


def refused_line(category: str) -> str:
    return REFUSED.format(category=category)


# -- places -------------------------------------------------------------------

#: A Mac's disk reads ``~/.SSH`` as ``~/.ssh``, and ``normcase`` leaves the letters alone there.
_FOLD_CASE = sys.platform == "darwin"


def _fold(path: str) -> str:
    return path.casefold() if _FOLD_CASE else os.path.normcase(path)


def canon(path: str) -> str:
    """The real, case-folded absolute path ("" when it cannot be read)."""
    try:
        # Linux and Mac: never tidy ".." by text first. The kernel follows a link and then goes up from where it landed.
        return _fold(os.path.realpath(os.path.abspath(path) if os.name == "nt" else path))
    except (OSError, ValueError):
        return ""


def inside_or_equal(child: str, parent: str) -> bool:
    if not child or not parent:
        return False
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:              # another drive
        return False


def home_dir() -> str:
    env = os.environ.get
    return (env("USERPROFILE") if os.name == "nt" else None) or os.path.expanduser("~")


def user_name() -> str:
    """The owner's account name (what is scrubbed from outgoing text)."""
    name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not name:
        name = os.path.basename(home_dir().rstrip("\\/"))
    return name.strip()


def _sensitive(home: str) -> list[tuple[str, str]]:
    """Her own sensitive places: keys, browser profiles, startup, personal."""
    env = os.environ.get
    j = os.path.join
    out: list[tuple[str, str]] = []
    for name in (".ssh", ".gnupg", ".aws", ".azure", ".kube", ".git-credentials", ".netrc",
                 "_netrc", ".npmrc", ".pypirc", ".claude", ".claude.json", ".docker",
                 j(".config", "gh"), j(".config", "gcloud"), j(".local", "share", "keyrings"),
                 j("Library", "Keychains")):
        out.append((j(home, name), KEYS))
    for name in ("Documents", "Desktop", "Pictures", "Downloads", "Videos", "Music",
                 j("OneDrive", "Documents"), j("OneDrive", "Desktop"),
                 j("OneDrive", "Pictures")):
        out.append((j(home, name), PERSONAL))
    for rel in (j(".config", "google-chrome"), j(".config", "chromium"),
                j(".config", "BraveSoftware"), j(".config", "microsoft-edge"), ".mozilla",
                j("snap", "firefox"), j("snap", "chromium"), j(".var", "app", "org.mozilla.firefox"),
                j(".var", "app", "com.google.Chrome"), j(".var", "app", "org.chromium.Chromium"),
                j("Library", "Application Support", "Google"),
                j("Library", "Application Support", "Firefox"),
                j("Library", "Application Support", "BraveSoftware"),
                j("Library", "Application Support", "Microsoft Edge"), j("Library", "Safari"),
                j("Library", "Cookies")):
        out.append((j(home, rel), BROWSER))
    # What runs by itself when she signs in: Linux and Mac have these where Windows has its Startup folder.
    for rel in (j(".config", "autostart"), j(".config", "systemd", "user"), j("Library", "LaunchAgents"),
                ".profile", ".bash_profile", ".bash_login", ".bashrc", ".zshrc", ".zprofile", ".zshenv", ".zlogin",
                ".xprofile", ".xinitrc", ".xsessionrc", ".pam_environment", j(".config", "fish", "config.fish")):
        out.append((j(home, rel), STARTUP))
    # $XDG_CONFIG_HOME and $XDG_DATA_HOME move the same places, when she set them.
    config, shared, runtime = env("XDG_CONFIG_HOME"), env("XDG_DATA_HOME"), env("XDG_RUNTIME_DIR")
    if config and os.path.isabs(config):
        out += [(j(config, "gh"), KEYS), (j(config, "gcloud"), KEYS), (j(config, "google-chrome"), BROWSER),
                (j(config, "chromium"), BROWSER), (j(config, "BraveSoftware"), BROWSER),
                (j(config, "microsoft-edge"), BROWSER), (j(config, "autostart"), STARTUP),
                (j(config, "systemd", "user"), STARTUP), (j(config, "fish", "config.fish"), STARTUP)]
    if shared and os.path.isabs(shared):
        out.append((j(shared, "keyrings"), KEYS))
    if os.name != "nt" and runtime and os.path.isabs(runtime):
        out.append((runtime, KEYS))                      # her sign-in agents listen here (ssh-agent, the keyring)
    appdata, local = env("APPDATA"), env("LOCALAPPDATA")
    if appdata:
        out += [(j(appdata, "Mozilla"), BROWSER), (j(appdata, "Opera Software"), BROWSER),
                (j(appdata, "Microsoft", "Credentials"), KEYS),
                (j(appdata, "Microsoft", "Protect"), KEYS),
                (j(appdata, "Microsoft", "Crypto"), KEYS),
                (j(appdata, "Microsoft", "SystemCertificates"), KEYS),
                (j(appdata, "GitHub CLI"), KEYS), (j(appdata, "gcloud"), KEYS),
                (j(appdata, "Microsoft", "Windows", "Start Menu"), STARTUP)]
    if local:
        out += [(j(local, "Google"), BROWSER), (j(local, "Microsoft", "Edge"), BROWSER),
                (j(local, "BraveSoftware"), BROWSER), (j(local, "Mozilla"), BROWSER),
                (j(local, "Chromium"), BROWSER),
                (j(local, "Microsoft", "Credentials"), KEYS),
                (j(local, "Microsoft", "Vault"), KEYS)]
    pd = env("ProgramData")
    if pd:
        out.append((j(pd, "Microsoft", "Windows", "Start Menu", "Programs", "StartUp"),
                    STARTUP))
    return out


class ZoneUnreadable(Exception):
    """A place Toolsmith must never touch could not be read: the zone refuses everything."""


def _json_paths(path: str, key: str) -> list[str]:
    """Paths from a settings file. Missing file: none. Unreadable or junk: raises (fail closed)."""
    import json
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    got = data.get(key) if isinstance(data, dict) else None
    if got is None:
        return []
    items = [got] if isinstance(got, str) else list(got)
    found = []
    for item in items:
        p = item.get("path") if isinstance(item, dict) else item
        if not isinstance(p, str) or not p.strip():
            raise ValueError("a place in settings is not a path")
        found.append(p)
    return found


def _folders(path: str, root: str) -> tuple[str, list[str]]:
    """Lane L3's ``settings/folders.json``: ``(data folder, linked folders)``. Missing file: the default
    data folder and none linked. There but unreadable, or any other shape: raises (fail closed)."""
    import json
    default = os.path.join(root, "data")
    if not os.path.exists(path):
        return default, []
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("folders.json is not an object")
    folder = data.get("data", default)
    if folder is None:
        folder = default
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("data is not a path")
    linked = data.get("linked", [])
    if linked is None:
        linked = []
    if not isinstance(linked, list) or not all(isinstance(x, str) and x.strip() for x in linked):
        raise ValueError("linked is not a list of paths")
    return folder, list(linked)


def _internal(state: Optional[str] = None) -> list[tuple[str, str]]:
    """The bot's own places. Raises :class:`ZoneUnreadable` if any can't be read (never drops one)."""
    try:
        from ..paths import data_root, state_dir
        root = state or str(state_dir())
        j = os.path.join
        settings = j(root, "settings")
        places: list[tuple[str, str]] = [(root, STATE), (j(root, "packages"), OTHER_APP),
                                         (j(root, "vault"), VAULT), (j(root, "vault.inbox"), VAULT)]
        places += [(p, VAULT) for p in _json_paths(j(settings, "vault.json"), "folder")]
        data_folder, linked = _folders(j(settings, "folders.json"), root)
        places.append((data_folder, DATA))
        if canon(data_folder) != canon(j(root, "data")):
            places.append((j(root, "data"), DATA))           # the default one, inside the bot folder
        places += [(p, LINKED) for p in linked]
        # The folder she chose for books and downloads (D103): only its store is a data place, never her other
        # files beside it. _folders has read this file as an object by now. Junk raises, like the rest.
        import json
        folders_json = j(settings, "folders.json")
        if os.path.exists(folders_json):
            with open(folders_json, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            chosen = saved.get("knowledge")
            if chosen is not None:
                if not isinstance(chosen, str) or not chosen.strip() or not os.path.isabs(chosen):
                    raise ValueError("knowledge is not a whole path")
                places.append((j(chosen, "knowledge"), DATA))
            # Each document she pinned (D125): one file. A folder that holds it is refused as one holding a data
            # place is. Not in _INSIDE_OK: that lets a folder of her code sit inside a place, and a file holds nothing.
            pinned = saved.get("pinned")
            if pinned is not None:
                if not isinstance(pinned, list) or not all(isinstance(x, str) and x.strip() and os.path.isabs(x)
                                                           for x in pinned):
                    raise ValueError("pinned is not a list of whole paths")
                places += [(p, PINNED) for p in pinned]
        # A move's two places, in any phase: a store left behind (leave_old), or one a stopped move is half way out of.
        journal = j(root, "index", "knowledge_move.json")
        if os.path.exists(journal):
            with open(journal, "r", encoding="utf-8") as fh:
                move = json.load(fh)
            if not isinstance(move, dict):
                raise ValueError("the move's record is not an object")
            for key in ("to_root", "from_root"):
                got = move.get(key)
                if got is not None:
                    if not isinstance(got, str) or not got.strip() or not os.path.isabs(got):
                        raise ValueError("a place in the move's record is not a whole path")
                    places.append((got, DATA))
        here = os.path.dirname(os.path.abspath(__file__))
        places.append((os.path.dirname(os.path.dirname(here)), CODE))      # core/ (the Core's code)
        # Every bot of hers on this computer and the machine's wrap key, whichever bot this try belongs to (D132).
        places += [(str(data_root()), KEYS), (str(data_root() / "machine.key"), KEYS)]
        return places
    except Exception as exc:  # noqa: BLE001 - fail closed (X23)
        raise ZoneUnreadable(type(exc).__name__) from exc


_TOOL_PROGRAMS = ("node", "npm", "npx", "pnpm", "yarn", "corepack", "python", "python3", "py",
                  "pip", "pip3", "go", "ruby", "gem", "bundle", "docker", "git", "bun", "deno",
                  "java", "cargo", "uv")


def _toolchains(home: str) -> list[str]:
    """Read-only places: the folders of the toolchain programs and their caches."""
    env = os.environ.get
    j = os.path.join
    out: list[str] = []
    for name in _TOOL_PROGRAMS:
        exe = shutil.which(name)
        if not exe:
            continue
        for p in {exe, os.path.realpath(exe)}:
            d = os.path.dirname(p)
            out.append(d)
            up = os.path.dirname(d)
            # ~/.local/bin holds no toolchain above it: ~/.local is her programs' own data.
            if os.path.basename(d).lower() in ("bin", "cmd", "scripts") and up != j(home, ".local"):
                out.append(up)
    out += [j(home, ".npm"), j(home, ".cache", "pip"), j(home, ".cache", "yarn"),
            j(home, ".cache", "pnpm"), j(home, ".local", "share", "pnpm"),
            j(home, "go", "pkg", "mod"), j(home, ".gem"), j(home, ".cargo", "registry"),
            j(home, "Library", "Caches", "pip"), j(home, "Library", "Caches", "Yarn")]
    appdata, la = env("APPDATA") or "", env("LOCALAPPDATA") or ""
    if appdata:
        out += [j(appdata, "npm-cache"), j(appdata, "npm")]
    if la:
        out += [j(la, "npm-cache"), j(la, "pip", "cache"), j(la, "pnpm"), j(la, "pnpm-cache"),
                j(la, "Yarn", "Cache"), j(la, "go-build")]
    for var in ("GOMODCACHE", "GOPATH", "PIP_CACHE_DIR", "npm_config_cache"):
        if env(var):
            out.append(env(var) or "")
    return out


@dataclass
class Zone:
    """One try's boundary. ``carve``: the try's own places (allowed even
    inside a denied area, like ``host_apps/<id>`` in the state folder)."""
    folder: str
    carve: list[str]
    deny: list[tuple[str, str]]
    toolchain: list[str]
    home: str
    scratch: str = ""
    env: dict = field(default_factory=dict)
    broken: bool = False
    deny_write: list = field(default_factory=list)   # [(path, category)]: reading is fine, writing never

    def classify(self, path: str, *, write: bool = False) -> tuple[str, str]:
        """``(ALLOW | DENY | OUTSIDE, category)`` for one path."""
        if self.broken:
            return DENY, UNREADABLE
        raw = str(path)
        if _stream_syntax(raw):
            return DENY, STREAM
        if _odd_prefix(raw):
            return self._classify_odd(raw, write)
        if _plain_device(raw):
            return ALLOW, ""                  # /dev/null and its like: no file, and nothing of hers
        if _through_a_process(raw):
            return DENY, UNCHECKABLE          # only the program it runs in knows where it leads
        real = canon(path)
        if not real:                          # Linux and Mac: a link it may not read, so where it leads is not known
            return (OUTSIDE, "") if os.name == "nt" else (DENY, UNCHECKABLE)
        written = os.path.abspath(path)       # the path's own words too: a link can hide what it is called
        if _python_start_hook(real) or _python_start_hook(written):
            return DENY, PYSTART              # wins over the try's own folder too
        if write and (_in_site_packages(real) or _in_site_packages(written)):
            return DENY, PYSTART              # any write into any site-packages, the try's own too
        system = _system_place(real) or _system_place(written)
        if write and any(inside_or_equal(real, p) for p, _c in self.deny_write):
            return DENY, SYSTEM if system else CODE
        if any(inside_or_equal(real, c) for c in self.carve):
            return ALLOW, ""
        if (write and system) or _device_place(real) or _device_place(written):
            return DENY, SYSTEM               # a device: read or written, as a device path is on Windows
        hits = [(p, cat) for p, cat in self.deny if inside_or_equal(real, p)]
        if hits:
            return DENY, max(hits, key=lambda h: len(h[0]))[1]
        # A folder that HOLDS one of them (the home folder, a drive, AppData).
        if os.path.dirname(real) == real or inside_or_equal(canon(self.home), real):
            return DENY, WHOLE
        held = [cat for p, cat in self.deny if inside_or_equal(p, real)]
        if held:
            return DENY, held[0]
        if not write and any(inside_or_equal(real, t) for t in self.toolchain):
            return ALLOW, ""
        return OUTSIDE, ""

    def _classify_odd(self, raw: str, write: bool) -> tuple[str, str]:
        r"""One odd path, as :func:`_odd_form` maps it. A write never passes, nor a form that can't be mapped. A read
        waits for her Yes at best: the own folder counts only after the real resolve, and is no outright allow.
        A name with a trailing dot or space is denied, unless it is the last name and leads nowhere but the own folder,
        both as ``\\?\`` reads it (the name as written) and as ``\\.\`` does (the dots and spaces taken off)."""
        kind, path = _odd_form(raw)
        if write or kind == _ODD_BAD:
            return DENY, UNCHECKABLE
        names = [x for x in path.split("\\") if x]
        hostile = any(_hostile_name(x) for x in names)
        if kind == _ODD_SHARE:                  # text only: reading it would reach a host the model named
            real = os.path.normcase(ntpath.normpath(path))
            if hostile:
                return DENY, UNCHECKABLE
            if _python_start_hook(real):
                return DENY, PYSTART
            hit = _raw_denied(real, self.deny, self.carve)   # a store on a share: reading it is refused too
            return (DENY, hit) if hit else (OUTSIDE, "")
        if any(_hostile_name(x) for x in names[:-1]):
            return DENY, UNCHECKABLE
        text = ntpath.normpath(path)
        reals = [_kernel_real(p) for p in dict.fromkeys((text, text.rstrip(". ")))]
        if not all(reals):
            return DENY, UNCHECKABLE
        seen = [os.path.normcase(text.rstrip(". "))] + reals
        if any(_python_start_hook(x) for x in seen):
            return DENY, PYSTART                # wins over the try's own folder too
        if hostile and not all(any(inside_or_equal(r, c) for c in self.carve) for r in reals):
            return DENY, UNCHECKABLE
        for x in seen:
            hit = _raw_denied(x, self.deny, self.carve)
            if hit:
                return DENY, hit
        for real in reals:
            how, cat = self.classify(real)
            if how == DENY:
                return DENY, cat
        return OUTSIDE, ""

    def refusal(self, path: str, *, write: bool = False) -> str:
        """The plain line when *path* is denied, else ""."""
        how, cat = self.classify(path, write=write)
        return refused_line(cat) if how == DENY else ""


#: Linux and Mac: folders of the computer itself. Writing there never runs in a try: it is her own step (03 3.5).
_SYSTEM_PLACES = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/boot", "/sys", "/proc", "/dev",
                  "/Library", "/System", "/private/etc")
#: The devices any program uses, that hold nothing and reach nothing.
_PLAIN_DEVICES = frozenset({"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/stdin", "/dev/stdout",
                            "/dev/stderr", "/dev/tty"})
#: The Docker engine's socket. Windows has its pipe, refused as a device path.
_ENGINE_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock", "/var/run/podman/podman.sock",
                   "/run/podman/podman.sock")


def _system_place(path: str) -> bool:
    if os.name == "nt":
        return False
    return any(inside_or_equal(path, _fold(p)) or inside_or_equal(path, canon(p)) for p in _SYSTEM_PLACES)


def _device_place(path: str) -> bool:
    return os.name != "nt" and inside_or_equal(path, _fold("/dev"))


def _plain_device(raw: str) -> bool:
    return os.name != "nt" and ".." not in raw.split("/") and _fold(os.path.normpath(raw)) in _PLAIN_DEVICES


def _through_a_process(path: str) -> bool:
    """*path* passes through ``/proc/self``, ``/proc/thread-self`` or ``/proc/<number>``, walked as the kernel walks it
    (each link followed, then ``..`` from where it landed). The check runs in the Core, so ``/proc/self/cwd`` here is
    not the program's, and another process's ``environ`` or ``mem`` holds what it holds: such a path is no place the
    check can read, like an odd path on Windows. ``/proc/cpuinfo`` is not one of these. Too many links counts as one."""
    if os.name == "nt":
        return False
    try:
        todo = os.path.join(os.getcwd(), path).split("/")
    except OSError:
        return True
    here: list[str] = []
    hops = 0
    while todo:
        name = todo.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            del here[-1:]
            continue
        here.append(name)
        if len(here) == 2 and here[0] == "proc" and (name in ("self", "thread-self") or name.isdigit()):
            return True
        try:
            target = os.readlink("/" + "/".join(here))
        except (OSError, ValueError):
            continue
        hops += 1
        if hops > 40:
            return True
        here.pop()
        if target.startswith("/"):
            here = []
        todo = target.split("/") + todo
    return False


_SITE_DIRS = ("site-packages", "dist-packages")
_HOOK_NAMES = ("sitecustomize.py", "usercustomize.py")


def _python_start_hook(real: str) -> bool:
    """A ``.pth`` file, or ``sitecustomize.py``/``usercustomize.py``, inside any site-packages folder."""
    if not real:
        return False
    parts = [x.lower() for x in re.split(r"[\\/]+", real) if x]
    if not any(x in _SITE_DIRS for x in parts[:-1]):
        return False
    name = parts[-1] if parts else ""
    return name.endswith(".pth") or name in _HOOK_NAMES


def _in_site_packages(real: str) -> bool:
    parts = [x.lower() for x in re.split(r"[\\/]+", real or "") if x]
    return any(x in _SITE_DIRS for x in parts)


def _odd_prefix(raw: str) -> bool:
    r"""``\\?\``, ``\\.\``, ``\??\`` and UNC paths (``\\localhost\C$``), with either slash: Windows reads ``/\`` and
    ``\/`` as it reads ``\\``. The checks can't follow them as written: :func:`_odd_form` maps them.
    Windows only: on Linux and Mac ``//home/x`` is ``/home/x``, and a backslash is a letter of a file name."""
    if os.name != "nt":
        return False
    s = raw.strip().strip("\"'")
    return s.replace("/", "\\").lower().startswith(("\\\\", "\\??\\")) or s.lower().startswith("\\device\\")


_ODD_LOCAL, _ODD_SHARE, _ODD_BAD = "local", "share", "bad"
_ADMIN_SHARE = re.compile(r"(?i)^(?:[a-z]\$|admin\$|ipc\$|print\$)$")


def _this_computer(host: str) -> bool:
    """*host* names this computer: the loopback in any spelling (``127.1``, ``0x7f.0.0.1``), or its own name.
    Text and this computer's own settings only: nothing is looked up on the network."""
    import socket
    name = host.lower().rstrip(". ")
    if name in ("localhost", "[::1]", "0--1.ipv6-literal.net"):
        return True
    try:
        addr = socket.inet_aton(name)
        return addr[0] == 127 or addr == b"\0\0\0\0"
    except (OSError, ValueError, UnicodeError):
        pass
    return name in {n.lower() for n in (os.environ.get("COMPUTERNAME"), socket.gethostname()) if n}


def _odd_form(raw: str) -> tuple[str, str]:
    r"""What an odd path really opens: ``(_ODD_LOCAL, "C:\x")``, this computer's own path, whatever the prefix, or
    a drive's admin share on this computer; ``(_ODD_SHARE, "\\host\share\x")``, a real share, text only; or
    ``(_ODD_BAD, "")``, which can't be mapped to a known place (``Volume{...}``, ``GLOBALROOT``, ``\Device\``, a device,
    an admin share elsewhere, which may still be this computer by another name). A trailing space stays: it is
    part of the name the kernel gets."""
    s = raw.lstrip().rstrip("\r\n").strip("\"'").replace("/", "\\")
    if s.lower().startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        rest = s[4:]
        if rest[:4].lower() != "unc\\":
            if not re.match(r"^[A-Za-z]:(?:\\|$)", rest):
                return _ODD_BAD, ""
            return _ODD_LOCAL, rest if len(rest) > 2 else rest + "\\"
        s = "\\\\" + rest[4:]
        if "\\" not in rest[4:].strip("\\"):
            return _ODD_BAD, ""                            # a long form names a share, or nothing
    elif not s.startswith("\\\\"):
        return _ODD_BAD, ""
    host, *more = s[2:].split("\\")
    more = [x for x in more if x]
    if host in ("?", ".", "??") or (not host and more):
        return _ODD_BAD, ""
    if not more:
        return _ODD_SHARE, "\\\\" + host                   # no share named: nothing here to open
    share = more[0].lower()
    if not share.strip(". "):
        return _ODD_BAD, ""
    if _this_computer(host):
        if re.match(r"^[a-z]\$$", share):
            return _ODD_LOCAL, share[0].upper() + ":\\" + "\\".join(more[1:])
        return _ODD_BAD, ""                                # a share of hers: only Windows knows where it leads
    if _ADMIN_SHARE.match(share):
        return _ODD_BAD, ""
    return _ODD_SHARE, "\\\\" + host + "\\" + "\\".join(more)


def _hostile_name(name: str) -> bool:
    """A path part with a trailing dot or space: plain paths can't name it, only an odd form can."""
    return name not in (".", "..") and name != name.rstrip(". ")


def _kernel_real(local: str) -> str:
    r"""Where a local path really leads, each name exactly as written (``\\?\`` keeps the kernel from dropping a
    trailing dot or space): links followed, 8.3 names made long, case-folded. "" when it can't be read."""
    if os.name != "nt":
        return os.path.normcase(local)
    try:
        got = os.path.realpath("\\\\?\\" + local)
    except (OSError, ValueError):
        return ""
    if got[:8].lower() == "\\\\?\\unc\\":
        got = "\\\\" + got[8:]                             # a link to a share, or a mapped drive
    elif re.match(r"^\\\\\?\\[A-Za-z]:", got):
        got = got[4:]
    return os.path.normcase(got)


def _raw_denied(real: str, deny: list[tuple[str, str]], carve: list[str]) -> str:
    """The category of a denied place *real* (whole, case-folded) sits in or holds, "" when none. Text only: a path
    inside the try's own folder (``carve``) is no hit on a place that holds that folder, as in ``classify``. A denied
    place inside the folder stays denied."""
    real = real.rstrip("\\/") + os.sep                     # by text: a share's root has no commonpath
    own = [c.rstrip("\\/") + os.sep for c in carve if c and real.startswith(c.rstrip("\\/") + os.sep)]  # whole path parts
    hits = [(p, cat) for p, cat in deny if p and (real.startswith(p.rstrip("\\/") + os.sep)
                                                  or (p.rstrip("\\/") + os.sep).startswith(real))
            and not any(c.startswith(p.rstrip("\\/") + os.sep) for c in own)]
    return max(hits, key=lambda h: len(h[0]))[1] if hits else ""


def _stream_syntax(raw: str) -> bool:
    r"""A path naming an NTFS stream: ``a.pth::$DATA``, ``a.txt:hidden``, ``dir\\x:y``.
    ``host:port``, ``file:12`` and a drive letter are not streams. Only NTFS has them: on Linux and Mac a colon is a
    letter of a file name."""
    if os.name != "nt":
        return False
    s = raw.strip().strip("\"'").replace("/", "\\")
    s = s.lstrip("\\")
    if s[:2] in ("?\\", ".\\"):
        s = s[2:]
    if re.match(r"^[A-Za-z]:", s):
        s = s[2:]
    if "::" in s or re.search(r":\$", s):
        return True
    if ":" not in s:
        return False
    parts = s.split("\\")
    if any(":" in seg for seg in parts[:-1]):
        return True
    m = re.match(r"^([^:]*):([^:]*)$", parts[-1])
    if not m:
        return True                            # several colons
    name, stream = m.groups()
    if not stream:
        return bool(name)                      # "a.pth:" (a trailing colon)
    if stream[0].isdigit():
        return False                           # host:port, file:line
    return "." in name or len(parts) > 1


_CONCAT = re.compile(r"""(?i)["']\s*\+\s*["'$(\[]|\$[\w:]+\s*\+\s*["'$]|\bjoin-path\b|::\s*(?:combine|concat|join)\b"""
                     r"""|\s-join\b|%\w+:~""")
_WRITE_WORDS = re.compile(r"""(?i)>|\b(?:copy|move|xcopy|robocopy|cp|mv|tee|install|ln|copy-item|move-item|cpi|mi|"""
                          r"""set-content|add-content|out-file|new-item|ni|mkdir|md|touch|sc|ac|rename-item|ren|"""
                          r"""rename)\b|-(?:outfile|destination|filepath|path|target)\b|::\s*write""")
_UNRESOLVED = re.compile(r"\$|%\w+%")


def _core_python() -> list[tuple[str, str]]:
    """The Python that runs the Core: writing in it changes the Core's own code."""
    import sys
    out = []
    for p in {sys.prefix, sys.base_prefix, getattr(sys, "exec_prefix", "")}:
        c = canon(p) if p else ""
        if c and os.path.dirname(c) != c:
            out.append((c, CODE))
    return out


def zone_for(folder: str, *, logs: str = "", scratch: str = "", state: Optional[str] = None) -> Zone:
    """The boundary of a try working in *folder* (its *logs* and *scratch*
    folders allowed too). If the bot's places can't be read, the zone is
    ``broken`` and refuses everything (fail closed, X23)."""
    home = home_dir()
    try:
        internal = _internal(state)
        root = canon(os.path.join(internal[0][0], "packages"))
        engine = [(p, ENGINE) for p in _ENGINE_SOCKETS] if os.name != "nt" else []
        deny = [(canon(p), cat) for p, cat in internal + _sensitive(home) + engine]
        if not root or not deny[0][0]:
            raise ZoneUnreadable("state")
    except ZoneUnreadable:
        return Zone(folder="", carve=[], deny=[], toolchain=[], home=home, scratch=scratch,
                    env=dict(os.environ), broken=True)
    deny = [(p, cat) for p, cat in deny if p]
    # A program in /bin or ~/bin: the folder above it is the whole disk or the home folder, never a toolchain.
    tools = [t for t in (canon(p) for p in _toolchains(home) if p)
             if t and os.path.dirname(t) != t and not inside_or_equal(canon(home), t)]
    zone = Zone(folder=canon(folder) if folder else "", carve=[], deny=deny,
                toolchain=[t for t in tools if t], home=home, scratch=scratch,
                env=dict(os.environ), deny_write=_core_python())
    # Only a real try folder is allowed: one folder directly under packages,
    # or a folder of her code that is not and does not hold a denied place.
    # Anything else allows nothing.
    real = zone.folder
    if real and (_is_try_folder(real, root) or not folder_problem(real, zone)):
        for p in (folder, logs, scratch):
            c = canon(p) if p else ""
            if c and (inside_or_equal(c, real) or _is_try_folder(c, root)):
                zone.carve.append(c)
        own = _own_scratch(real, root, scratch, internal[0][0])
        if own:
            zone.carve.append(own)
    return zone


def _own_scratch(real: str, root: str, scratch: str, state: str) -> str:
    r"""The try's own scratch folder, ``<bot>\logs\toolsmith\<try folder name>\scratch``, when *scratch*
    is exactly that folder; else "" (D70). It is where the try's ``/tmp``, ``$TMP`` and ``%TEMP%`` point
    (:func:`expand`), so a compose file with a ``/tmp`` tmpfs line is no longer read as BuildBot's own
    files. Nothing else under ``logs`` is allowed: not the try's server logs, not another try's scratch,
    not the audit log. A link anywhere on the way (``logs``, ``toolsmith``, the try's folder, ``scratch``)
    makes its real path differ from the plain one, so it is not allowed."""
    if not scratch or not _is_try_folder(real, root):
        return ""
    base = canon(state)
    if not base:
        return ""
    want = os.path.normcase(os.path.join(base, "logs", "toolsmith", os.path.basename(real), "scratch"))
    got = canon(scratch)
    return got if got and got == want else ""


def _is_try_folder(real: str, root: str) -> bool:
    """A folder directly under packages that is not one of the Core's own (``.staging``)."""
    return bool(root) and os.path.dirname(real) == root and not os.path.basename(real).startswith(".")


def folder_problem(path: str, zone: Optional[Zone] = None) -> str:
    """A refused line when a folder of her code cannot be Toolsmith's folder:
    inside or holding a denied place (inside her data folder, a linked folder
    or Documents is fine: only that subfolder is used). "" when it can."""
    real = canon(path)
    z = zone or zone_for("")
    if z.broken:
        return refused_line(UNREADABLE)
    if not real:
        return refused_line(WHOLE)
    if os.path.dirname(real) == real or inside_or_equal(canon(z.home), real):
        return refused_line(WHOLE)
    for p, cat in z.deny:
        if inside_or_equal(real, p) and (cat not in _INSIDE_OK or real == p):
            return refused_line(cat)
        if inside_or_equal(p, real) and real != p:
            return refused_line(cat)
    return ""


# -- reading a command the way a shell would --

_LISTS = frozenset({"PATH", "PYTHONPATH", "NODE_PATH", "PSMODULEPATH", "PATHEXT",
                    "MANPATH", "INFOPATH", "LD_LIBRARY_PATH"})
_VAR = re.compile(r"\$\{env:(\w+)\}|\$env:(\w+)|\$\{(\w+)(?::?-([^}]*))?\}|\$(\w+)|%(\w+)%",
                  re.I)


def expand(text: str, zone: Zone, cwd: str, extra: Optional[dict] = None) -> str:
    """``~``, ``$HOME``, ``${X:-d}``, ``$env:X``, ``%X%`` and ``$PWD`` put in.
    Unknown names stay as they are."""
    env = {k.upper(): v for k, v in (zone.env or os.environ).items()}
    env.update({k.upper(): str(v) for k, v in (extra or {}).items()})
    env.setdefault("HOME", zone.home)
    env["PWD"] = cwd
    if zone.scratch:
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = zone.scratch

    def var(m: Any) -> str:
        name = next(g for g in (m.group(1), m.group(2), m.group(3), m.group(5), m.group(6))
                    if g)
        if name.upper() in _LISTS:
            return m.group(0)       # a search path list names no file to touch
        if name.upper() in env:
            return env[name.upper()]
        if m.group(4) is not None:
            return m.group(4)
        return m.group(0)
    text = _VAR.sub(var, text)
    return re.sub(r"(?:(?<=^)|(?<=[\s\"'=:(]))~(?=[\\/\s\"';|&)]|$)", lambda _m: zone.home,
                  text)


_OPS = re.compile(r"\|\||&&|>>|[12&]?>&?[12]?|>\||[|;&()<>\n`]")


def _lex(text: str) -> list[tuple[str, str]]:
    """``[(kind, text)]``: ``word`` (quotes taken off, backslashes kept) or ``op``."""
    out: list[tuple[str, str]] = []
    i, n = 0, len(text)
    word: Optional[str] = None
    while i < n:
        ch = text[i]
        if ch in " \t\r":
            if word is not None:
                out.append(("word", word))
                word = None
            i += 1
            continue
        if ch in "\"'":
            end = text.find(ch, i + 1)
            end = n if end < 0 else end
            word = (word or "") + text[i + 1:end]
            i = end + 1
            continue
        if text.startswith("$(", i):
            if word is not None:
                out.append(("word", word))
                word = None
            out.append(("op", "("))
            i += 2
            continue
        m = _OPS.match(text, i)
        if m and not (ch.isdigit() and word is not None):
            if word is not None:
                out.append(("word", word))
                word = None
            out.append(("op", m.group(0)))
            i = m.end()
            continue
        word = (word or "") + ch
        i += 1
    if word is not None:
        out.append(("word", word))
    return out


_CD = frozenset({"cd", "pushd", "chdir", "set-location", "sl", "push-location"})
_COPY = frozenset({"cp", "mv", "copy", "move", "xcopy", "robocopy", "rsync", "scp", "install",
                   "ln", "copy-item", "move-item", "cpi", "mi", "tee", "tee-object",
                   "out-file", "set-content", "add-content", "new-item", "ni", "mkdir", "md",
                   "touch", "rm", "del", "erase", "rmdir", "rd", "remove-item", "ri"})
_WRITE_FLAGS = frozenset({"-DestinationPath", "--target-directory",
                          "-o", "--output", "-O", "--output-document", "-C", "--directory",
                          "-f", "--file", "-OutFile", "-outfile", "-Destination",
                          "-destination", "-Path", "-path", "-FilePath", "-filepath",
                          "--prefix", "--target", "-t", "--cache", "--cache-dir"})
_MOUNT_FLAGS = frozenset({"-v", "--volume", "--mount"})
#: Programs that change every path they are given (a first word that is a mode names nothing).
_CHANGES_EACH = frozenset({"rm", "del", "erase", "rmdir", "rd", "remove-item", "ri", "mkdir", "md", "touch", "chmod",
                           "chown", "chgrp", "chattr", "chflags", "setfacl", "truncate", "shred", "unlink", "xattr"})
#: Write flags that mean something else for other programs.
_PROG_WRITE_FLAGS = {"unzip": ("-d",), "wget": ("-P", "--directory-prefix")}
#: PowerShell names flags in any case and may glue the value on (``-Destination:C:\x``).
_WRITE_FLAGS_CI = frozenset(f.lower() for f in _WRITE_FLAGS if len(f) > 2 and not f.startswith("--"))
#: cmd-style programs whose ``/s``, ``/E``, ``/MIR``, ``/R:3`` are switches, never paths.
_CMD_SWITCH_PROGS = frozenset({"robocopy", "xcopy", "copy", "move", "del", "erase", "dir", "rd", "rmdir", "md",
                               "mkdir", "type", "attrib", "findstr", "find", "more", "sort", "ren", "rename",
                               "icacls", "takeown", "where", "tree", "fc", "comp", "taskkill", "tskill",
                               "wmic", "schtasks", "tasklist", "reg", "sc", "setx", "msiexec", "netsh",
                               "bcdedit", "shutdown", "net"})
#: ``/s`` in cmd, ``//s`` in Git Bash (which would turn ``/s`` into a path): a switch, never a drive or a share.
_CMD_SWITCH = re.compile(r"^/{1,2}[A-Za-z?][A-Za-z0-9?:+*.-]{0,15}$")


def _is_write_flag(flag: str) -> bool:
    return flag in _WRITE_FLAGS or (len(flag) > 2 and not flag.startswith("--") and flag.lower() in _WRITE_FLAGS_CI)
_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")
_GLOB = re.compile(r"[*?\[]")


def _to_host(word: str, cwd: str, zone: Zone, *, mount: bool = False) -> Optional[str]:
    """The host path a word names, or None when it is not a path here."""
    w = word.strip(" \t\r\n")          # only what the shell and Windows drop: a no-break space is part of a name
    if os.name == "nt" and _odd_prefix(w):
        # Before the checks below: its ``?`` is no wildcard, ``C://`` no web address, and ``/\`` no Git path.
        w = word.lstrip()
        head = re.match(r"^[\\/]{2}[?.][\\/]|^[\\/]\?\?[\\/]", w)
        g = _GLOB.search(w, head.end() if head else 0)
        return w[:max(w.rfind("\\", 0, g.start()), w.rfind("/", 0, g.start())) + 1] if g else w
    if not w or "://" in w or w.startswith(("-", "@", "$")) or re.match(r"^[\w.-]+@[\w.-]+:", w):
        return None
    g = _GLOB.search(w)
    if g:
        w = w[:g.start()]
        w = w if w.endswith(("/", "\\")) or not w else os.path.dirname(w) or "."
    if os.name == "nt":
        m = re.match(r"^/(?:mnt/)?([A-Za-z])(?:/|$)(.*)$", w)
        if m:
            return f"{m.group(1).upper()}:\\" + m.group(2).replace("/", "\\")
        if re.match(r"^/tmp(?:/|$)", w):
            return os.path.join(zone.scratch or cwd, w[5:]) if zone.scratch else None
        if w == "/":
            return os.path.abspath(os.sep)      # Git's root reaches every drive
        if w.startswith("/") and not w.startswith("//"):
            return None             # Git's own files or a container path
        if _DRIVE.match(w):
            return w if len(w) > 2 else w + "\\"
    else:
        if w.startswith("~"):
            w = os.path.expanduser(w)   # ~other/.ssh: another account's home folder
        if w.startswith("/"):
            return _posix_tmp(w, zone) if not mount and re.match(r"^/+tmp(?:/|$)", w) else w
    if mount and not w.startswith((".", "~")) and not re.search(r"[\\/]", w):
        return None                 # a named volume
    if not (re.search(r"[\\/]", w) or w in (".", "..") or w.startswith(".")):
        return os.path.join(cwd, w)
    return os.path.join(cwd, w)


def _posix_tmp(w: str, zone: Zone) -> str:
    """A try's ``/tmp`` is its scratch folder, as on Windows (D70), unless it leads somewhere else: through a link out
    of ``/tmp`` or into a denied place (a bot folder under ``/tmp``), the path is read as what it opens."""
    if not zone.scratch or _through_a_process(w):
        return w
    real = canon(w)
    if not inside_or_equal(real, canon("/tmp")) or any(inside_or_equal(real, p) for p, _c in zone.deny):
        return w
    return os.path.join(zone.scratch, w.lstrip("/")[4:].lstrip("/"))


def _mount_sources(flag: str, value: str) -> list[str]:
    if flag == "--mount":
        m = re.search(r"(?:^|,)(?:source|src)=([^,]+)", value)
        return [m.group(1)] if m else []
    m = re.match(r"^([A-Za-z]:[\\/][^:]*|[^:]+)(?::|$)", value)
    return [m.group(1)] if m else []


def _split_list(part: str) -> list[str]:
    """A path list split: ``;`` (Windows) and ``:``, never a drive's colon."""
    if os.name != "nt":
        return part.split(":")
    out: list[str] = []
    for chunk in part.split(";"):
        if _odd_prefix(chunk):
            out.append(chunk)               # its colon is a drive's, after the prefix
            continue
        cur = ""
        for i, ch in enumerate(chunk):
            drive = len(cur) == 1 and cur.isalpha() and chunk[i + 1:i + 2] in ("\\", "/", "")
            if ch == ":" and not drive:
                out.append(cur)
                cur = ""
            else:
                cur += ch
        out.append(cur)
    return out


def _pieces(word: str) -> list[str]:
    """A word and its parts: ``--opt=value``, ``A=B``, ``a:b`` path lists."""
    out = [word]
    if "=" in word:
        out.append(word.split("=", 1)[1])
    for part in list(out):
        out += [b for b in _split_list(part) if b and b != part]
    return list(dict.fromkeys(out))


def _home_or_posix_rule(zone: Zone, path: str, write: bool = False) -> bool:
    """Waits for her Yes. On Linux and macOS a path outside the home folder that names nothing on this computer and
    is not written to is a path inside a container (``/app/data``). A write, or anything that is there, waits as on
    Windows; denied places are still denied."""
    return os.name == "nt" or write or inside_or_equal(canon(path), canon(zone.home)) or os.path.lexists(path)


def _mount_of_system(path: str, zone: Zone) -> bool:
    """A mount can be written to, so it is judged as a write: a system folder on Linux or a Mac, even one a toolchain
    may read, or all of ``/tmp``, where other programs keep their sign-in sockets (ssh-agent)."""
    if os.name == "nt":
        return False
    return canon(path) == canon("/tmp") or zone.classify(path, write=True) == (DENY, SYSTEM)


_OWN_CLI = re.compile(r"(?i)(?:^|[\s;&|(])(?:puffpai(?:\.cmd|\.exe)?(?=\s|$)|"
                      r"-m\s+puffpai(?:_core)?\b|import\s+puffpai(?:_core)?\b|from\s+puffpai(?:_core)?\b)")


def command_check(command: str, zone: Zone, cwd: str = "", *, owner_step: bool = False) -> tuple[str, list[str]]:
    """``(refused line, outside paths)`` for one terminal command: the line
    when a path it names (read, written, ``cd``, mounted) is denied; else ""
    and the paths outside the allowed places (they wait for her yes).
    *owner_step*: the text of a "Your turn" step she runs herself, where a
    registry place (``HKLM:\\...``, 03 3.5 example 3) is not a file path."""
    if zone.broken:
        return refused_line(UNREADABLE), []
    cwd = cwd or zone.folder
    if _OWN_CLI.search(command):
        return refused_line(CODE), []
    if re.search(r"\b(?:docker|podman)\b", command) and _ENGINE.search(command):
        return refused_line(ENGINE), []
    if _CONCAT.search(command):
        # A path built by joining strings: the check can't see where it lands.
        if _WRITE_WORDS.search(command):
            return refused_line(UNCHECKABLE), []
        concat_outside = [command]
    else:
        concat_outside = []
    toks = _lex(expand(command, zone, cwd))
    outside: list[str] = []
    seg: list[str] = []
    redirect = ""

    def host(word: str, cwd_: str, zone_: Zone, *, mount: bool = False) -> Optional[str]:
        if owner_step and _REG_PATH.match(str(word).strip().strip("\"'")):
            return None
        return _to_host(word, cwd_, zone_, mount=mount)

    def check(path: Optional[str], *, write: bool = False, mount: bool = False) -> str:
        if not path:
            return ""
        how, cat = zone.classify(path, write=write)
        if how == DENY:
            return refused_line(cat)
        if mount and how != ALLOW:
            real = "" if _odd_prefix(path) else canon(path)     # never look up a host the model named
            if real and (os.path.dirname(real) == real
                         or inside_or_equal(canon(zone.home), real)):
                return refused_line(WHOLE)
        if mount and _mount_of_system(path, zone):
            return refused_line(SYSTEM)
        if how == OUTSIDE and _home_or_posix_rule(zone, path, write):
            outside.append(path)
        return ""

    def end_segment() -> str:
        nonlocal cwd
        if not seg:
            return ""
        prog = os.path.basename(seg[0].replace("\\", "/")).lower()
        prog = re.sub(r"\.(exe|cmd|bat)$", "", prog)
        words = seg[1:]
        if os.name == "nt" and prog in _CMD_SWITCH_PROGS:    # on Linux and Mac /etc is a folder, never a switch
            words = [w for w in words if not _CMD_SWITCH.match(w)]   # /s, /E, /MIR: switches, not drives
        if prog in _CD:
            target = next((w for w in words if not w.startswith("-")), zone.home)
            dest = host(target, cwd, zone) or target
            why = check(dest)
            if why:
                return why
            if zone.classify(dest)[0] == ALLOW:
                cwd = dest
            return ""
        why = check(host(seg[0], cwd, zone)) if re.search(r"[\\/~]", seg[0]) else ""
        if why:
            return why
        positional = [w for w in words if not w.startswith("-")]
        write_targets: set[str] = set()
        if prog in _COPY and positional:
            write_targets.add(positional[1] if prog == "robocopy" and len(positional) > 1 else positional[-1])
        if prog in _CHANGES_EACH:
            write_targets.update(positional)
        if prog == "dd":
            write_targets.update(w for w in words if w.startswith("of="))
        if prog == "expand-archive" and len(positional) >= 2:
            write_targets.add(positional[1])                     # Expand-Archive <zip> <destination>
        if prog == "git" and positional[:1] == ["clone"] and len(positional) >= 3:
            write_targets.add(positional[2])                     # git clone <url> <folder>
        if prog in _COPY and len(positional) >= 2:
            # A folder destination means the folder plus each source's name (both are writes).
            if prog == "robocopy":
                dest, sources = positional[1], positional[2:] or [positional[0]]
            else:
                dest, sources = positional[-1], positional[:-1]
            dest_host = host(dest, cwd, zone)
            if dest_host and not _odd_prefix(dest_host) and (dest.endswith(("\\", "/")) or os.path.isdir(dest_host)):
                for src in sources:
                    name = os.path.basename(src.rstrip("\\/"))
                    if name:
                        why = check(os.path.join(dest_host, name), write=True)
                        if why:
                            return why
        for k, w in enumerate(words):
            prev = words[k - 1] if k else ""
            is_mount = prev in _MOUNT_FLAGS or w.startswith(("--volume=", "--mount="))
            if is_mount:
                value = w.split("=", 1)[1] if w.startswith("--") and "=" in w \
                    and prev not in _MOUNT_FLAGS else w
                for src in _mount_sources("--mount" if "--mount" in (prev, w[:7]) else "-v",
                                          value):
                    why = check(host(src, cwd, zone, mount=True), mount=True)
                    if why:
                        return why
                continue
            if w.startswith("-") and ":" in w[2:] and not w.startswith("--"):
                flag, value = w.split(":", 1)                     # -Destination:C:\x (one word)
                why = check(host(value, cwd, zone), write=_is_write_flag(flag))
                if why:
                    return why
                if _is_write_flag(flag) and _UNRESOLVED.search(value):
                    return refused_line(UNCHECKABLE)
                continue
            write = _is_write_flag(prev) or w in write_targets or prev in _PROG_WRITE_FLAGS.get(prog, ())
            if write and _UNRESOLVED.search(w):
                return refused_line(UNCHECKABLE)       # a write target the check can't resolve
            for piece in _pieces(w):
                why = check(host(piece, cwd, zone), write=write)
                if why:
                    return why
        return ""

    for kind, text in toks:
        if kind == "op":
            if text in (">", ">>", ">|", "1>", "2>", "&>") or text.endswith(">") and \
                    "&" not in text[1:]:
                redirect = "w"
                continue
            if text == "<":
                redirect = "r"
                continue
            why = end_segment()
            if why:
                return why, []
            seg = []
            continue
        if redirect:
            if redirect == "w" and _UNRESOLVED.search(text):
                return refused_line(UNCHECKABLE), []
            why = check(host(text, cwd, zone), write=redirect == "w")
            redirect = ""
            if why:
                return why, []
            continue
        seg.append(text)
    why = end_segment()
    if why:
        return why, []
    return "", list(dict.fromkeys(outside + concat_outside))


# -- compose files ------------------------------------------------------------

def compose_refusal(text: str, compose_dir: str, zone: Zone,
                    extra_env: Optional[dict] = None) -> str:
    """The refused line when a compose file mounts or reads a denied place
    (or the whole home folder or a drive), else ""."""
    if zone.broken:
        return refused_line(UNREADABLE)
    if _ENGINE.search(text):
        return refused_line(ENGINE)
    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - unreadable: the line check below still runs
        data = None
    sources: list[tuple[str, bool]] = []
    if isinstance(data, dict):
        for svc in (data.get("services") or {}).values() if isinstance(
                data.get("services"), dict) else []:
            if not isinstance(svc, dict):
                continue
            for v in svc.get("volumes") or []:
                if isinstance(v, str):
                    sources += [(s, True) for s in _mount_sources("-v", v)]
                elif isinstance(v, dict) and v.get("source"):
                    sources.append((str(v["source"]), True))
            env_files = svc.get("env_file") or []
            for e in [env_files] if isinstance(env_files, (str, dict)) else env_files:
                p = e.get("path") if isinstance(e, dict) else e
                if p:
                    sources.append((str(p), False))
            build = svc.get("build")
            ctx = build.get("context") if isinstance(build, dict) else build
            if isinstance(ctx, str) and "://" not in ctx:
                sources.append((ctx, True))
        for key in ("volumes", "secrets", "configs"):
            block = data.get(key)
            if isinstance(block, dict):
                for v in block.values():
                    if isinstance(v, dict):
                        opts = v.get("driver_opts") or {}
                        if isinstance(opts, dict) and opts.get("device"):
                            sources.append((str(opts["device"]), True))
                        if v.get("file"):
                            sources.append((str(v["file"]), False))
    # The plain-text look too (no YAML reader here, or a file it cannot read):
    # every list item that looks like a mount, every source/device/file line.
    for m in re.finditer(r"(?m)^\s*-\s*[\"']?([^\s\"'#]+:[^\s\"'#]+)", text):
        if os.name != "nt" and re.match(r"^[^:]+:[\w-]+=", m.group(1)):
            continue                                    # a tmpfs line (/tmp:size=64m): its settings, no folder here
        sources += [(s, True) for s in _mount_sources("-v", m.group(1))]
    for m in re.finditer(r"(?m)^\s*-?\s*(?:source|device|file|context|env_file|path)\s*:"
                         r"\s*[\"']?([^\s\"'#]+)", text):
        sources.append((m.group(1), True))
    for raw, mount in sources:
        src = expand(raw, zone, compose_dir, extra_env)
        if mount and not src.startswith((".", "~", "/", "\\")) and not _DRIVE.match(src):
            continue                                    # a named volume
        path = _to_host(src, compose_dir, zone, mount=True) if mount else \
            _to_host(src, compose_dir, zone)
        if not path:
            continue
        how, cat = zone.classify(path)
        if how == DENY:
            return refused_line(cat)
        real = "" if _odd_prefix(path) else canon(path)         # never look up a host the model named
        if mount and real and (os.path.dirname(real) == real
                               or inside_or_equal(canon(zone.home), real)):
            return refused_line(WHOLE)
        if mount and _mount_of_system(path, zone):
            return refused_line(SYSTEM)
    return ""


# -- what leaves: her name out, and back in on the way in ---------------------

def _name_re(name: str) -> Optional[re.Pattern]:
    if not name or name.lower() == "you":
        return None
    if len(name) < 4:
        return re.compile(r"(?i)((?:[\\/]Users|[\\/]home)[\\/])" + re.escape(name)
                          + r"(?![\w-])")
    return re.compile(r"(?i)(?<![\w.-])()" + re.escape(name) + r"(?![\w-])")


def scrub(value: Any, name: Optional[str] = None) -> Any:
    """*value* (text, or dicts and lists of it) with her account name as "you"."""
    pat = _name_re(user_name() if name is None else name)
    if pat is None:
        return value

    def one(v: Any) -> Any:
        if isinstance(v, str):
            return pat.sub(lambda m: m.group(1) + "you", v)
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(one(x) for x in v)
        return v
    return one(value)


def unscrub(value: Any, name: Optional[str] = None) -> Any:
    """The model's arguments with ``Users/you`` (as it was shown) back to her
    real home folder, so a path it copied still works here."""
    real = user_name() if name is None else name
    if _name_re(real) is None:
        return value
    pat = re.compile(r"(?i)((?:^|[\\/]|[\s\"'=:])(?:Users|home)[\\/])you(?=[\\/\s\"';|&)]|$)")

    def one(v: Any) -> Any:
        if isinstance(v, str):
            return pat.sub(lambda m: m.group(1) + real, v)
        if isinstance(v, dict):
            return {k: one(x) for k, x in v.items()}
        if isinstance(v, list):
            return [one(x) for x in v]
        return v
    return one(value)


# -- the shell Toolsmith gets -------------------------------------------------

def shell_env(base: dict, scratch: str = "", shims: str = "") -> dict:
    """*base* without the Core's own command line and settings: no
    ``PUFFPAI_*`` names, no bot folder or Core code on the path, its temp
    folders in the try's scratch folder."""
    from ..paths import state_dir
    state = canon(str(state_dir()))
    code = canon(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    env = {k: v for k, v in base.items() if not k.upper().startswith("PUFFPAI")}
    for key in [k for k in env if k.upper() in ("PATH", "PYTHONPATH", "NODE_PATH")]:
        keep = [p for p in str(env[key]).split(os.pathsep)
                if p and not inside_or_equal(canon(p), state)
                and not (code and inside_or_equal(canon(p), code))]
        if key.upper() == "PATH" and shims:
            keep.insert(0, shims)
        if keep:
            env[key] = os.pathsep.join(keep)
        else:
            env.pop(key)
    if scratch:
        os.makedirs(scratch, exist_ok=True)
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = scratch
    return env


def python_shims(folder: str, exe: str) -> str:
    """A folder with ``python`` and ``python3`` only (never BuildBot's own
    ``puffpai`` command line); its path."""
    import sys
    exe = exe or sys.executable or "python"
    os.makedirs(folder, exist_ok=True)
    texts = {}
    for name in ("python3", "python"):
        texts[name] = '#!/bin/sh\nexec "{}" "$@"\n'.format(exe.replace("\\", "/"))
        if os.name == "nt":
            texts[name + ".cmd"] = '@"{}" %*\r\n'.format(exe)
    for name, text in texts.items():
        path = os.path.join(folder, name)
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            if os.name != "nt":
                os.chmod(path, 0o755)
        except OSError:
            pass
    return folder


# -- stopping processes, and changing the computer (lane I2a security fix) -----
#
# A try ran ``taskkill /F /PID <n>`` and it passed: a try could end ANY program on the computer by its number.
# Now a command that stops a process runs only when every process it names is in this try's own job object
# (the broker's jobs, ``own_pids``); by name or pattern (``/IM``, ``-Name``, ``pkill``) never. Toolsmith stops
# its own servers with its ``stop_server`` tool (the broker ends the job), never with a shell kill.
# A command that changes the computer outside the try (installs for the whole computer, the registry,
# scheduled tasks, services, ``setx``, Python outside the app's venv) never runs in a try: it is a "Your turn"
# step the owner runs herself (03 3.5). Both are read the way the zone reads paths: a boundary for the model's
# own commands, not a sandbox. A script the try writes and runs can still do either (honest limit).

KILL_BY_NAME = ("Toolsmith may not stop programs by name or pattern: that could end any program on this "
                "computer. Stop your own servers with stop_server.")
KILL_NOT_OURS = ("Toolsmith may stop only processes this try started (in its own job), and that is not one of "
                 "them, so nothing was stopped. Stop your own servers with stop_server.")
KILL_UNCHECKABLE = ("That stops a process in a way PuffPAI can't check is this try's own, so it was refused. "
                    "Stop your own servers with stop_server.")
HIDDEN = "That command is encoded, so PuffPAI can't check what it does, and it was refused."
MACHINE = ("That changes this computer outside the app's folder ({what}), so Toolsmith does not run it. "
           "If the app needs it, ask the owner (ask_owner with kind run_command, or install_program): "
           "the owner runs it.")

M_INSTALL = "an install for the whole computer"
M_PYTHON = "a Python install"
M_PIP = "Python packages outside the app's own .venv"
M_REGISTRY = "the Windows registry"
M_TASK = "a scheduled task"
M_SERVICE = "a Windows service or account"
M_ENV = "a setting for every program"
M_POWER = "a restart or shut down"
M_SYSTEM = "a Windows system setting"
M_NETWORK = "the network or firewall settings"
M_SECURITY = "Windows security settings"
M_SERVICE_HERE = "a service or account on this computer"
M_SYSTEM_HERE = "a system setting of this computer"

#: Programs that run the program named after them (so ``cmd /c taskkill ...`` is read as taskkill).
_WRAPPERS = frozenset({"cmd", "start", "call", "sudo", "gsudo", "doas", "su", "env", "nohup", "exec", "command",
                       "builtin", "xargs", "timeout", "time", "nice", "runas", "wsl", "bash", "sh", "zsh", "dash",
                       "powershell", "pwsh", "invoke-expression", "iex", "start-process", "saps",
                       "invoke-command", "icm", "invoke-item", "ii", "explorer", "%", "foreach", "foreach-object",
                       ".", "watch", "busybox", "winpty", "conhost", "forfiles", "parallel", "npx", "pnpx", "bunx",
                       "uvx", "pnpm", "yarn", "pipx", "cscript", "wscript", "pkexec", "setsid", "stdbuf", "ionice",
                       "chroot", "unshare", "nsenter", "flock", "caffeinate", "open", "xdg-open"})
_KILL_PROGS = frozenset({"taskkill", "tskill", "kill", "stop-process", "spps", "pskill", "pskill64", "ntsd",
                         "cdb", "wmic"})
#: Always by name, by pattern or by port: never allowed.
_KILL_BY_NAME = frozenset({"pkill", "killall", "killall5", "skill", "xkill", "kill-port", "fkill", "fkill-cli",
                           "pgrep"})
_KILL_API = re.compile(r"(?i)\.kill\s*\(|\bos\.kill(?:pg)?\b|\bprocess\.kill\b|\bTerminateProcess\b|"
                       r"\.terminate\s*\(|\bInvoke-(?:Cim|Wmi)Method\b[^|;\n]*\bterminate\b|"
                       r"\bRemove-(?:CimInstance|WmiObject)\b|\bGetProcessBy(?:Id|Name)\b|"
                       r"\bsignal\.SIG(?:KILL|TERM)\b|\bDebugActiveProcess\b|\bNtTerminateProcess\b")
_SIGNAL = re.compile(r"(?i)^-(?:sig)?(?:kill|term|int|hup|quit|stop|cont|usr1|usr2|abrt|alrm|\d+)$")
_PS_OK_FLAGS = ("-force", "-passthru", "-whatif", "-confirm")


def _prog_name(word: str) -> str:
    """The program a word names: its file name, lower case, without ``.exe`` and cmd's ``^`` escapes."""
    w = str(word or "").strip().strip("\"'").replace("^", "").replace("\\", "/").rstrip("/")
    name = w.rsplit("/", 1)[-1].lower()
    name = re.sub(r"(?<=.)@[\w.^~-]*$", "", name)          # npx pnpm@9.1.0
    return re.sub(r"\.(?:exe|com|cmd|bat|ps1)$", "", name)


def _segments(text: str, depth: int = 0) -> list[list[str]]:
    """Each simple command's words, and those of every quoted inner command (``-Command "..."``, ``-c '...'``)."""
    out: list[list[str]] = []
    cur: list[str] = []
    for kind, t in _lex(text):
        if kind == "op":
            if cur:
                out.append(cur)
                cur = []
            continue
        cur.append(t)
        if depth < 4 and re.search(r"[\s;|&]", t.strip()):
            out += _segments(t, depth + 1)
    if cur:
        out.append(cur)
    return out


def _programs(text: str):
    """``(program, its words)`` for each program a command line runs, behind wrappers too."""
    for seg in _segments(text):
        starts = range(len(seg)) if _prog_name(seg[0]) in _WRAPPERS else range(1)
        for i in starts:
            yield _prog_name(seg[i]), seg[i + 1:]


def _pid_word(word: str) -> Optional[list[int]]:
    """``123`` or ``123,456``: the process numbers, else None."""
    parts = [p for p in re.split(r"[,\s]+", str(word).strip().strip("\"'()@")) if p]
    if parts and all(p.isdigit() for p in parts):
        return [int(p) for p in parts]
    return None


def _switch(word: str) -> tuple[str, str]:
    """``/PID:5``, ``//PID``, ``-pid``: ``("pid", "5")``."""
    key, _sep, val = re.sub(r"^[/-]+", "", word).lower().partition(":")
    return key, val


def _kill_targets(name: str, args: list[str]) -> tuple[str, list[int]]:
    """``("", pids)`` when every target is a process number; else ``(refused line, [])``."""
    pids: list[int] = []
    i = 0
    if name == "taskkill":
        while i < len(args):
            a = args[i]
            if not a.startswith(("/", "-")):
                return KILL_UNCHECKABLE, []
            key, val = _switch(a)
            if key in ("f", "t"):
                i += 1
                continue
            if key == "pid":
                v = val or (args[i + 1] if i + 1 < len(args) else "")
                i += 1 if val else 2
                got = _pid_word(v)
                if got is None:
                    return KILL_UNCHECKABLE, []
                pids += got
                continue
            if key in ("im", "fi"):
                return KILL_BY_NAME, []
            return KILL_UNCHECKABLE, []            # /S /U /P (another computer), or a switch PuffPAI does not know
        return "", pids
    if name in ("tskill", "pskill", "pskill64"):
        for a in args:
            if a.startswith(("/", "-")):
                key, _v = _switch(a)
                if key in ("a", "v", "t", "nobanner"):
                    continue
                return KILL_UNCHECKABLE, []        # another server, session or user
            got = _pid_word(a)
            if got is None:
                return (KILL_UNCHECKABLE if a.startswith("\\\\") else KILL_BY_NAME), []
            pids += got
        return "", pids
    if name in ("ntsd", "cdb"):
        for k, a in enumerate(args):
            low = a.lower()
            if low in ("-pn", "-psn"):
                return KILL_BY_NAME, []
            if low.startswith("-k") or low == "-premote":
                return KILL_UNCHECKABLE, []
            if low == "-p":
                got = _pid_word(args[k + 1]) if k + 1 < len(args) else None
                if got is None:
                    return KILL_UNCHECKABLE, []
                pids += got
        return "", pids
    if name == "wmic":
        rest = " ".join(args).strip()
        if re.search(r"(?i)(?:^|\s)/(?:node|user|password|authority)\b", rest):
            return KILL_UNCHECKABLE, []
        m = re.fullmatch(r"(?i)process\s+(?:where\s+\(?\s*(?:processid|handle)\s*=\s*['\"]?(\d+)['\"]?\s*\)?|(\d+))"
                         r"\s+(?:delete|call\s+terminate(?:\s+\d+)?)(?:\s+/nointeractive)?", rest)
        if not m:
            return (KILL_BY_NAME if re.search(r"(?i)\b(?:name|caption|commandline|executablepath)\b", rest)
                    else KILL_UNCHECKABLE), []
        return "", [int(m.group(1) or m.group(2))]
    # kill (Git Bash, or PowerShell's alias of Stop-Process), Stop-Process, spps
    after_dashes = False
    while i < len(args):
        a = args[i]
        low = a.lower()
        if after_dashes or not a.startswith("-"):
            got = _pid_word(a)
            if got is None:
                return (KILL_BY_NAME if re.match(r"^[A-Za-z][\w.*-]*$", a) else KILL_UNCHECKABLE), []
            pids += got
            i += 1
            continue
        if a == "--":
            after_dashes = True
            i += 1
            continue
        flag, _sep, val = low.partition(":")
        if flag == "-id":
            v = val or (args[i + 1] if i + 1 < len(args) else "")
            i += 1 if val else 2
            got = _pid_word(v)
            if got is None:
                return KILL_UNCHECKABLE, []
            pids += got
            continue
        if flag == "-s" and name == "kill":            # kill -s TERM <pid>
            i += 2
            continue
        if _SIGNAL.match(flag) or (len(flag) > 1 and any(f.startswith(flag) for f in _PS_OK_FLAGS)):
            i += 1
            continue
        if len(flag) > 1 and any(f.startswith(flag) for f in ("-name", "-processname")):
            return KILL_BY_NAME, []
        return KILL_UNCHECKABLE, []                     # -InputObject, -l, a flag PuffPAI does not know
    return "", pids


def _encoded(text: str) -> bool:
    """``powershell -EncodedCommand ...`` (any short form): what it runs can't be read."""
    for name, args in _programs(text):
        if name in ("powershell", "pwsh"):
            for a in args:
                low = a.lower().split(":", 1)[0]
                if low.startswith("-") and len(low) >= 2 and ("-encodedcommand".startswith(low) or low == "-ec"):
                    return True
    return False


def process_refusal(command: str, own_pids: Any = None) -> str:
    """The refused line when *command* stops a process that is not in this try's own job (or by name,
    or in a way that can't be checked), else "". *own_pids*: the pids in the try's job objects."""
    text = str(command or "")
    if _encoded(text):
        return HIDDEN
    if _KILL_API.search(text):
        return KILL_UNCHECKABLE
    try:
        own = {int(p) for p in (own_pids or ())}
    except (TypeError, ValueError):
        own = set()
    for name, args in _programs(text):
        if name in _KILL_BY_NAME or (name == "fuser" and any(a.startswith("-") and "k" in a for a in args)):
            return KILL_BY_NAME
        if name not in _KILL_PROGS:
            continue
        if name == "wmic" and not any(a.lower() in ("delete", "terminate") for a in args):
            continue                                   # wmic process list, get: a read
        line, pids = _kill_targets(name, args)
        if line:
            return line
        if not pids:
            return KILL_UNCHECKABLE                    # its targets come from a pipe, a job spec, or nowhere
        if any(p <= 0 or p not in own for p in pids):
            return KILL_NOT_OURS
    return ""


_REG_PATH = re.compile(r"(?i)(?<![\w-])(?:HK(?:LM|CU|CR|U|CC)|HKEY_[A-Z_]+)(?::|[\\/])|\bRegistry::")
_REG_WRITE = re.compile(r"(?i)\b(?:New|Set|Remove|Rename|Copy|Move|Clear)-Item(?:Property)?\b|"
                        r"(?<![\w-])(?:ni|si|sp|rp|ri|cpp|mp|rnp|clp|cli|cpi|mi|del|rm|erase|rd|rmdir)(?![\w-])")
_MACHINE_TEXT = (
    (re.compile(r"(?i)\bMicrosoft\.Win32\.Registry\b|\bwinreg\.(?:Set|Create|Delete)"), M_REGISTRY),
    (re.compile(r"(?i)SetEnvironmentVariable\b[^;|\n]*\b(?:User|Machine)\b"), M_ENV),
    (re.compile(r"(?i)\bPIP_(?:REQUIRE_VIRTUALENV|USER|TARGET|PREFIX|ROOT|ISOLATED|CONFIG_FILE|"
                r"BREAK_SYSTEM_PACKAGES)\b|\bUV_(?:SYSTEM_PYTHON|BREAK_SYSTEM_PACKAGES)\b"), M_PIP),
)
_ALWAYS = {
    "msiexec": M_INSTALL, "add-appxpackage": M_INSTALL, "add-appprovisionedpackage": M_INSTALL,
    "install-module": M_INSTALL, "install-package": M_INSTALL, "install-script": M_INSTALL,
    "install-psresource": M_INSTALL, "update-module": M_INSTALL, "install-windowsfeature": M_SYSTEM,
    "enable-windowsoptionalfeature": M_SYSTEM, "disable-windowsoptionalfeature": M_SYSTEM,
    "add-windowscapability": M_SYSTEM, "dism": M_SYSTEM, "bcdedit": M_SYSTEM, "bootcfg": M_SYSTEM,
    "rundll32": M_SYSTEM, "regedit": M_REGISTRY, "regedt32": M_REGISTRY, "regini": M_REGISTRY,
    "setx": M_ENV, "shutdown": M_POWER, "restart-computer": M_POWER, "stop-computer": M_POWER,
    "logoff": M_POWER, "rename-computer": M_SYSTEM, "add-computer": M_SYSTEM,
    "register-scheduledtask": M_TASK, "unregister-scheduledtask": M_TASK, "set-scheduledtask": M_TASK,
    "enable-scheduledtask": M_TASK, "disable-scheduledtask": M_TASK, "start-scheduledtask": M_TASK,
    "stop-scheduledtask": M_TASK, "new-service": M_SERVICE, "set-service": M_SERVICE,
    "remove-service": M_SERVICE, "start-service": M_SERVICE, "stop-service": M_SERVICE,
    "restart-service": M_SERVICE, "suspend-service": M_SERVICE, "resume-service": M_SERVICE,
    "set-mppreference": M_SECURITY, "add-mppreference": M_SECURITY, "remove-mppreference": M_SECURITY,
    "new-netfirewallrule": M_NETWORK, "set-netfirewallrule": M_NETWORK, "remove-netfirewallrule": M_NETWORK,
    "enable-netfirewallrule": M_NETWORK, "disable-netfirewallrule": M_NETWORK,
    "set-netfirewallprofile": M_NETWORK, "set-dnsclientserveraddress": M_NETWORK,
    "new-netipaddress": M_NETWORK, "set-netconnectionprofile": M_NETWORK, "pymanager": M_PYTHON,
    # Their Linux and Mac equals (D132), and running as someone else first.
    "sudo": M_SYSTEM_HERE, "gsudo": M_SYSTEM_HERE, "runas": M_SYSTEM_HERE, "doas": M_SYSTEM_HERE, "su": M_SYSTEM_HERE,
    "pkexec": M_SYSTEM_HERE, "chroot": M_SYSTEM_HERE, "nsenter": M_SYSTEM_HERE, "unshare": M_SYSTEM_HERE,
    "at": M_TASK, "batch": M_TASK, "reboot": M_POWER, "poweroff": M_POWER, "halt": M_POWER, "init": M_POWER,
    "update-rc.d": M_SERVICE_HERE, "useradd": M_SERVICE_HERE, "userdel": M_SERVICE_HERE, "usermod": M_SERVICE_HERE,
    "adduser": M_SERVICE_HERE, "passwd": M_SERVICE_HERE, "chsh": M_SERVICE_HERE, "visudo": M_SERVICE_HERE,
    "dscl": M_SERVICE_HERE, "modprobe": M_SYSTEM_HERE, "insmod": M_SYSTEM_HERE, "rmmod": M_SYSTEM_HERE,
    "mount": M_SYSTEM_HERE, "umount": M_SYSTEM_HERE, "setcap": M_SYSTEM_HERE, "csrutil": M_SYSTEM_HERE,
    "spctl": M_SYSTEM_HERE, "installer": M_INSTALL, "softwareupdate": M_INSTALL, "iptables": M_NETWORK,
    "ip6tables": M_NETWORK, "nft": M_NETWORK, "ufw": M_NETWORK, "firewall-cmd": M_NETWORK, "pfctl": M_NETWORK,
    "networksetup": M_NETWORK,
}
#: Package managers for the whole computer: only these reads run in a try.
_PM_READS = {"winget": {"", "search", "find", "show", "view", "list", "ls", "hash", "validate", "export"},
             "choco": {"", "list", "search", "find", "info", "outdated", "help"},
             "scoop": {"", "list", "search", "info", "status", "which", "help", "prefix", "cat", "checkup"},
             "apt": {"", "search", "show", "list", "policy"}, "apt-get": {""},
             "dnf": {"", "search", "info", "list"}, "yum": {"", "search", "info", "list"},
             "zypper": {"", "search", "info"}, "apk": {"", "search", "info", "list"},
             "brew": {"", "search", "info", "list", "outdated", "deps", "config", "doctor"},
             "snap": {"", "find", "info", "list"}, "flatpak": {"", "search", "info", "list"}}
#: Those whose first flag says what they do: only these reads run in a try.
_PM_FLAG_READS = {"pacman": ("-q", "-ss", "-si", "--query", "--version"),
                  "dpkg": ("-l", "-s", "--list", "--status", "--search", "--version"),
                  "rpm": ("-q", "--query", "--version")}
_SYSTEMCTL_READS = frozenset({"", "status", "show", "cat", "list-units", "list-unit-files", "is-active", "is-enabled"})
_LAUNCHCTL_READS = frozenset({"", "list", "print", "version", "help"})
_SC_WRITES = frozenset({"create", "config", "delete", "failure", "failureflag", "sdset", "description", "privs",
                        "sidtype", "start", "stop", "pause", "continue", "control", "triggerinfo",
                        "managedaccount", "preferrednode"})
_NET_WRITES = frozenset({"start", "stop", "pause", "continue", "user", "localgroup", "group", "share", "use",
                         "accounts", "config", "session", "file"})
_PIP = re.compile(r"^pip\d*(?:\.\d+)?$")
_PYTHON = re.compile(r"^(?:python\d*(?:\.\d+)?|pythonw|py)$")


def _first(args: list[str]) -> str:
    """The first word that is not a flag or switch, lower case ("" when none)."""
    return next((a.lower() for a in args if not a.startswith(("-", "/"))), "")


def _inside(value: str, folder: str, cwd: str) -> bool:
    if not folder or not value or _UNRESOLVED.search(value) or _odd_prefix(value):
        return False
    base = canon(folder)
    return bool(base) and inside_or_equal(canon(os.path.join(cwd or folder, value)), base)


def _pip_change(args: list[str], folder: str, cwd: str) -> str:
    """*args* after ``pip``: "" when it stays in the app's folder, else what it changes."""
    sub = _first(args)
    if sub == "config" and any(a.lower() in ("set", "edit", "unset") for a in args):
        return M_PIP
    if sub not in ("install", "download", "wheel", "uninstall"):
        return ""
    for k, a in enumerate(args):
        low = a.lower()
        flag, eq, _val = low.partition("=")
        if flag in ("--user", "--isolated", "--break-system-packages", "--system"):
            return M_PIP
        value: Optional[str] = None
        if flag in ("--target", "-t", "--prefix", "--root"):
            value = a.split("=", 1)[1] if eq else (args[k + 1] if k + 1 < len(args) else "")
        elif low.startswith("-t") and not low.startswith("--") and len(low) > 2:
            value = a[2:]                              # -tC:\elsewhere
        if value is not None and not _inside(value, folder, cwd):
            return M_PIP
    return ""


def machine_refusal(command: str, folder: str = "", cwd: str = "") -> str:
    """The refused line when *command* changes the computer outside the try in *folder*, else ""."""
    text = str(command or "")
    if _REG_PATH.search(text) and _REG_WRITE.search(text):
        return MACHINE.format(what=M_REGISTRY)
    for pat, what in _MACHINE_TEXT:
        if pat.search(text):
            return MACHINE.format(what=what)
    for name, args in _programs(text):
        lows = [a.lower() for a in args]
        sub = _first(args)
        what = _ALWAYS.get(name, "")
        if not what and re.search(r"\.(?:msi|msix|msixbundle|appx|appxbundle|msp)$", name):
            what = M_INSTALL
        if not what and name in _PM_READS and sub not in _PM_READS[name]:
            what = M_INSTALL
        if not what and name in _PM_FLAG_READS and not (lows and lows[0].startswith(_PM_FLAG_READS[name])):
            what = M_INSTALL
        if not what and name == "systemctl" and sub not in _SYSTEMCTL_READS:
            what = M_SERVICE_HERE
        if not what and name == "launchctl" and sub not in _LAUNCHCTL_READS:
            what = M_SERVICE_HERE
        if not what and name == "service" and lows != ["--status-all"] and "status" not in lows:
            what = M_SERVICE_HERE
        if not what and name == "crontab" and lows != ["-l"]:
            what = M_TASK
        if not what and name == "py" and sub in ("install", "uninstall"):
            what = M_PYTHON
        if not what and name == "reg" and sub in ("add", "delete", "import", "restore", "load", "unload", "copy"):
            what = M_REGISTRY
        if not what and name == "schtasks" and any(_switch(a)[0] in ("create", "change", "delete", "run", "end")
                                                   for a in args if a.startswith(("/", "-"))):
            what = M_TASK
        if not what and name == "sc" and sub in _SC_WRITES:
            what = M_SERVICE
        if not what and name in ("net", "net1") and sub in _NET_WRITES:
            what = M_SERVICE
        if not what and name == "netsh" and any(x in ("add", "set", "delete", "reset", "install", "uninstall",
                                                      "import") for x in lows):
            what = M_NETWORK
        if not what and name == "route" and sub in ("add", "delete", "change", "-f"):
            what = M_NETWORK
        if not what and name == "set-executionpolicy" and not any(
                x == "process" for x in lows):
            what = M_SECURITY
        if not what and name == "wsl" and any(x in ("--install", "--unregister", "--set-default", "-s",
                                                    "--set-version", "--set-default-version", "--import",
                                                    "--shutdown", "--terminate", "-t", "--update",
                                                    "--uninstall") for x in lows):
            what = M_SYSTEM
        if not what and name in ("npm", "pnpm", "bun", "corepack") and any(
                x in ("-g", "--global", "--location=global") for x in lows):
            what = M_INSTALL
        if not what and name == "npm" and "--location" in lows and "global" in lows:
            what = M_INSTALL
        if not what and name in ("npm", "pnpm", "yarn") and sub == "link" and \
                _first(args[lows.index("link") + 1:]) == "":
            what = M_INSTALL                           # a bare ``npm link`` links into the global folder
        if not what and name == "yarn" and sub == "global":
            what = M_INSTALL
        if not what and name == "corepack" and sub in ("enable", "disable"):
            what = M_INSTALL                           # it writes into Node's own folder
        if not what and name == "pipx" and sub in ("install", "inject", "upgrade", "upgrade-all", "reinstall",
                                                   "reinstall-all", "ensurepath", "uninstall"):
            what = M_PIP
        if not what and name == "uv":
            if lows[:2] in (["tool", "install"], ["tool", "upgrade"], ["python", "install"]):
                what = M_PYTHON if lows[0] == "python" else M_PIP
            elif lows[:1] == ["pip"]:
                what = _pip_change(args[1:], folder, cwd)
        if not what and _PIP.match(name):
            what = _pip_change(args, folder, cwd)
        if not what and _PYTHON.match(name) and "-m" in lows:
            k = lows.index("-m")
            if lows[k + 1:k + 2] == ["pip"]:
                what = _pip_change(args[k + 2:], folder, cwd)
        if what:
            return MACHINE.format(what=what)
    return ""


# -- the one call (lane L6's tools and the Core's exec broker) ---------------

@dataclass(frozen=True)
class Verdict:
    verdict: str          # allow | deny | ask (outside: waits for her Yes)
    line: str = ""        # the plain line for a deny; never a real path
    outside: tuple = ()   # paths that wait for her Yes


def check(target: str, *, folder: str, kind: str = "path", write: bool = False, cwd: str = "",
          logs: str = "", scratch: str = "", zone: Optional[Zone] = None, own_pids: Any = None,
          owner_step: bool = False) -> Verdict:
    """``check(path | command | compose text)`` for a try in *folder*. Fail closed: a zone
    that can't be built, an unknown kind, or a check that raises, is ``deny``.

    A command that stops a process passes only when every process it names is in *own_pids* (the try's
    own job objects; none given = none of its own). A command that changes the computer outside the
    try is denied, unless *owner_step*: then it is the text of a "Your turn" step she runs herself
    (a stop is still denied there: Toolsmith stops its own servers with stop_server)."""
    try:
        z = zone or zone_for(folder, logs=logs, scratch=scratch)
        if z.broken:
            return Verdict("deny", refused_line(UNREADABLE))
        if kind == "path":
            how, cat = z.classify(target, write=write)
            if how == DENY:
                return Verdict("deny", refused_line(cat))
            return Verdict("allow") if how == ALLOW else Verdict("ask", outside=(target,))
        if kind == "command":
            here = cwd or z.folder or folder
            line = ""
            for text in dict.fromkeys((str(target), expand(target, z, here))):   # as written, and expanded
                line = line or process_refusal(text, () if owner_step else own_pids)
                if not line and not owner_step:
                    line = machine_refusal(text, folder=z.folder or folder, cwd=here)
            if line:
                return Verdict("deny", line)
            line, outside = command_check(target, z, cwd, owner_step=owner_step)
            if line:
                return Verdict("deny", line)
            return Verdict("ask", outside=tuple(outside)) if outside else Verdict("allow")
        if kind == "compose":
            line = compose_refusal(target, cwd or z.folder, z)
            return Verdict("deny", line) if line else Verdict("allow")
        return Verdict("deny", refused_line(UNREADABLE))
    except Exception:  # noqa: BLE001 - a check that breaks refuses
        return Verdict("deny", refused_line(UNREADABLE))


__all__ = ["ALLOW", "BROWSER", "CODE", "DATA", "DENY", "KEYS", "LINKED", "OTHER_APP",
           "OUTSIDE", "PERSONAL", "PINNED", "PYSTART", "REFUSED", "STREAM", "SYSTEM", "UNCHECKABLE", "STARTUP", "STATE", "VAULT", "WHOLE", "Zone",
           "command_check", "compose_refusal", "expand", "folder_problem", "python_shims",
           "refused_line", "scrub", "shell_env", "unscrub", "zone_for", "check", "Verdict", "ZoneUnreadable",
           "UNREADABLE", "KILL_BY_NAME", "KILL_NOT_OURS", "KILL_UNCHECKABLE", "HIDDEN", "MACHINE",
           "process_refusal", "machine_refusal"]

