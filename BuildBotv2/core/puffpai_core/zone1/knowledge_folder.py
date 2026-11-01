r"""The one check for the folder she picks for books and downloads (D103).

``settings\folders.json`` ``knowledge`` names the folder she chose (the parent). The files always live in
``<chosen>\knowledge`` (the *store*), so her other files there, or a whole drive like ``E:\``, are never walked.

- :func:`problem`: why a folder may not be chosen, one plain line. Rules are checked against the store, so ``E:\``
  and ``C:\Users\me`` are fine (the store is a new folder there), and a folder that holds the vault only higher up
  is fine too. Links and odd spellings are checked on the chosen folder and the store, from the drive down.
- :func:`still_safe`: the cheap check again, before every write, for what can change after the choice.
- :func:`is_link`, :func:`inside_problem`: the mover never follows or copies a link, below the store too.
- :func:`source_problem`: the place files are moved out of, or into when that is the default place.
- The marker, ``<store>\.puffpai-knowledge.json``: which bot this store belongs to. A hash only, no name, no path.

A check that can't finish refuses. Nothing here calls a network host, except a share she typed herself.

Honest limits. This is not a sandbox.
- The swap race: a program running as the owner can swap a folder for a junction between a check and the write.
- A network name that points back at this computer (a hosts file, a DNS name) can't be told without asking the
  network, so only this computer's own names and addresses are refused.
- A move record edited by hand is believed by the mover: the state folder is hers, and only she can write there.
- A settings folder her own account is denied looks missing to Toolsmith's zone, as the vault's settings already do.
- A far bot this computer can't name, with a custom data folder and no marker in it, can't be told from a plain folder.
- :func:`still_safe` runs the whole of :func:`problem` once per folder per process, so a rule that starts to apply
  later (another bot appears there) is seen at the next start. Links, the marker and the vault are seen every time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import threading
from pathlib import Path

from . import fetched
from .folders import _overlap, _vault_places, canon

#: The folder inside the chosen one that holds everything.
STORE = "knowledge"
#: Longest path taken, written or expanded.
MAX_PATH = 1000

NO_FOLDER = "No folder was given."
WHOLE_PATH = "Use the folder's whole path, like E:\\Books."
ODD_PATH = "That path is written in a way BuildBot can't check. Use the folder's normal path, like E:\\Books."
THIS_PC_SHARE = "That is a share on this computer. Use the folder's real path, like E:\\Books."
ADMIN_SHARE = "That is a hidden admin share. Use a normal share, or pick another folder."
NET_GONE = "That network folder is not there. Turn it on, or pick another folder."
LINK = "That folder goes through a link to somewhere else. Pick the real folder."
NOT_FOLDER = "That path goes through a file, not a folder. Pick a folder."
CANT_CHECK = "BuildBot can't check that folder, so it isn't used. Pick a different one."
VAULT_UNREADABLE = "BuildBot can't read where your vault is kept, so no folder is used right now."
VAULT = "That folder holds your vault, or is inside it, so it can't be used."
SETTINGS_UNREADABLE = "BuildBot can't read its folder settings, so no folder is used right now."
OWN = "That is this bot's own folder, or overlaps it, so it can't be used."
DATA = "A folder inside your data folder, or one that holds it, can't be used. The data folder itself is fine."
LINKED = "That overlaps a folder you linked, so it can't be used. Pick a different one."
OTHER_BOT = "That folder belongs to another bot. Pick a different one."
OTHER_UNREADABLE = "BuildBot can't read another bot's settings, so no folder is used right now."
MARKER_JUNK = (f"The note BuildBot keeps in that folder can't be read, so it isn't used. Delete {fetched.MARKER} "
               "there to use it again.")
SOURCE_LINK = ("Your books and downloads go through a link to somewhere else, so nothing was moved. Remove the link "
               "first.")
SOURCE_VAULT = "Your books and downloads overlap your vault, so nothing was moved."
CODE = "That is BuildBot's own code folder, so it can't be used."
SYSTEM = "That is a system folder, so it can't be used. Pick a different one."
SECRETS = "That folder holds your keys, browser profiles or startup items, so it can't be used."

_NAME_SURROGATE = 0x20000000
_BAD_CHARS = frozenset('<>"|?*')
#: What ``expandvars`` would still act on: it could point somewhere else later. ``$Recycle.Bin`` is its own rule.
_VAR = re.compile(r"%[^%\\/]+%|\$\{|\$(?!recycle\.bin(?:[\\/]|$))\w", re.I)
_DOS = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                 | {f"{d}{n}" for d in ("COM", "LPT") for n in "123456789\u00b9\u00b2\u00b3"})
#: On any drive, by name.
_DRIVE_FOLDERS = frozenset({"$recycle.bin", "system volume information", "recovery"})
#: Checked even when the environment says nothing, or says something else.
_LITERAL_ROOT = "C:\\"
_LITERAL_SYSTEM = ("C:\\Windows", "C:\\Windows.old", "C:\\Program Files", "C:\\Program Files (x86)",
                   "C:\\ProgramData", "C:\\inetpub")
_LITERAL_USERS = ("C:\\Users",)
_POSIX_SYSTEM = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr", "/Library",
                 "/System")
#: A stat or settings read that failed: the check refuses.
_UNREADABLE = (OSError, ValueError, TypeError, AttributeError)
#: (bot, store) pairs that passed the whole of :func:`problem` in this process.
_passed: set[tuple[str, str]] = set()


# -- links ---------------------------------------------------------------------------------------------------

def _stat_is_link(st: os.stat_result) -> bool:
    if os.name != "nt":
        return stat.S_ISLNK(st.st_mode)
    return bool(st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT and st.st_reparse_tag & _NAME_SURROGATE)


def is_link(path: Path | str) -> bool:
    """A symlink, junction, mount point or any name-surrogate reparse point. A cloud placeholder (OneDrive's tags
    have that bit clear) is not one. A path that can't be read is False."""
    try:
        text = os.path.normpath(os.fspath(path))
        if os.name != "nt":
            return os.path.islink(text) or os.path.ismount(text)
        return _stat_is_link(os.stat(text, follow_symlinks=False))
    except (OSError, ValueError, TypeError):
        return False


def _way_problem(path: str) -> str:
    """Why the way to *path* is not safe, "" when it is: every part that exists, from the drive down. A part that
    can't be read refuses. On Linux a drive is mounted on a folder, so a mount point is fine on the way there."""
    drive, rest = os.path.splitdrive(path)
    cur = drive + os.sep
    for name in [""] + [p for p in re.split(r"[\\/]+" if os.name == "nt" else "/+", rest) if p]:
        cur = os.path.join(cur, name) if name else cur
        try:
            st = os.stat(cur, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return ""                                  # nothing below this is there yet
        except (OSError, ValueError):
            return CANT_CHECK
        if _stat_is_link(st):
            return LINK
        if not stat.S_ISDIR(st.st_mode):
            return NOT_FOLDER
    return ""


def inside_problem(store: Path | str, rel: Path | str) -> str:
    """Why the way below *store* to *rel* is not safe, "" when it is: every part that exists, the last one too. The
    mover asks before it writes or removes anything, since a part may be a junction to anywhere."""
    cur = os.fspath(store)
    for name in re.split(r"[\\/]+" if os.name == "nt" else "/+", os.fspath(rel)):
        if name in ("", "."):
            continue
        if name == "..":
            return CANT_CHECK
        cur = os.path.join(cur, name)
        try:
            st = os.stat(cur, follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            return ""
        except (OSError, ValueError):
            return CANT_CHECK
        if _stat_is_link(st):
            return LINK
    return ""


def _drive_line(path: str) -> str:
    """The plain line when the drive or the share of *path* (from :func:`_written`) is not there."""
    if os.name != "nt":
        return ""
    drive = os.path.splitdrive(path)[0]
    if os.path.isdir(drive + "\\"):
        return ""
    if drive.endswith(":"):
        return f"Drive {drive[0].upper()}: is not there. Plug it in, or pick another folder."
    return NET_GONE


# -- one spelling --------------------------------------------------------------------------------------------

def _this_computer(host: str) -> bool:
    r"""``\\localhost``, ``\\127.1``, this computer's own name: a share there is a second name for a folder here, so
    every rule below would miss it. This computer's own names and addresses only, nothing asked of the network."""
    h = host.lower().split("@", 1)[0]
    if h in ("localhost", "wsl$", "wsl.localhost") or h.endswith(".ipv6-literal.net"):
        return True
    names = {os.environ.get("COMPUTERNAME", "").lower(), socket.gethostname().lower()} - {""}
    if h in names or h.split(".", 1)[0] in names:
        return True
    try:
        packed = socket.inet_aton(h)
    except OSError:
        return False
    return packed[0] == 127 or packed == b"\0\0\0\0" or socket.inet_ntoa(packed) in _own_addresses()


def _own_addresses() -> set[str]:
    """What this computer's own name resolves to. A failure is just no extra addresses."""
    try:
        return {str(info[4][0]).lower() for info in socket.getaddrinfo(socket.gethostname(), None)}
    except OSError:
        return set()


def _written_nt(text: str) -> tuple[str, str]:
    s = text.replace("/", "\\")
    body = s.lstrip("\\")
    lead = len(s) - len(body)
    if lead >= 3 or (lead == 2 and body[:1] in ("?", ".")) or (lead and body.lower().startswith(("??\\", "?\\"))) \
            or (lead == 1 and body.lower().startswith("device\\")):
        return ODD_PATH, ""
    if lead == 1 or (not lead and not re.match(r"^[A-Za-z]:\\", s)):
        return WHOLE_PATH, ""
    parts = [p for p in re.split(r"\\+", body if lead else s[3:]) if p]
    if any(c in _BAD_CHARS for c in s) or ":" in (s if lead else s[2:]):
        return ODD_PATH, ""                            # wildcards, and an NTFS stream: any colon after the drive
    for part in parts:
        if part in (".", ".."):
            continue
        if part[-1] in " ." or part.split(".", 1)[0].rstrip(" ").upper() in _DOS:
            return ODD_PATH, ""
    if not lead:
        return "", os.path.abspath(s[:3] + "\\".join(parts))
    if len(parts) < 2 or parts[0] in (".", "..") or parts[1] in (".", ".."):
        return WHOLE_PATH, ""
    if _this_computer(parts[0]):
        return THIS_PC_SHARE, ""
    if parts[1].endswith("$"):
        return ADMIN_SHARE, ""                         # C$ is the whole of that computer's system drive
    return "", os.path.abspath("\\\\" + "\\".join(parts))


def _written(folder: Path | str | None) -> tuple[str, str]:
    """``(why not, the path as written)``: variables and ``~`` put in, ``..`` and repeated separators taken out, no
    link followed, nothing read from disk."""
    raw = "" if folder is None else str(folder)
    if not raw.strip():
        return NO_FOLDER, ""
    text = os.path.expandvars(os.path.expanduser(raw))
    for t in (raw, text):
        if len(t) > MAX_PATH or any(ord(c) < 32 or 127 <= ord(c) < 160 for c in t):
            return ODD_PATH, ""
    if os.name == "nt":
        got = _written_nt(text)
    else:
        got = ("", os.path.abspath(text)) if os.path.isabs(text) else (WHOLE_PATH, "")
    return (WHOLE_PATH, "") if not got[0] and _VAR.search(text) else got


def _split_existing(path: str) -> tuple[str, list[str]]:
    """The nearest part of *path* that is there, and the names below it that are not yet."""
    cur, tail = path, []
    while not os.path.lexists(cur):
        up = os.path.dirname(cur)
        if up == cur:
            break
        tail.append(os.path.basename(cur))
        cur = up
    return cur, tail[::-1]


def _canon_new(path: Path | str) -> str:
    """:func:`folders.canon` for a path that need not exist yet: the nearest parent that is there, then the rest."""
    cur, tail = _split_existing(os.path.abspath(os.path.expandvars(os.path.expanduser(str(path)))))
    return os.path.normcase(os.path.join(canon(cur), *tail))


def _as_written(path: str) -> str:
    """*path* in the same spelling as :func:`_canon_new`, 8.3 names made long, but no link followed. If the two
    differ, the path resolves somewhere else (``subst``, a mapped drive, a link)."""
    cur, tail = _split_existing(path)
    if os.name == "nt":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetLongPathNameW(cur, buf, 32768):
                cur = buf.value
        except (OSError, AttributeError, ValueError):
            pass
    return os.path.normcase(os.path.normpath(os.path.join(cur, *tail)))


def _resolves_elsewhere(real: str, written: str) -> str:
    r"""Why *written* can't be used given where it really is (*real*, from :func:`_canon_new`), "" when it can. A mapped
    drive is the one fine difference: ``Z:\x`` is really ``\\nas\share\x``, the same names after the share. That
    share then answers to the rules a typed share does. ``subst`` and links resolve to somewhere else: refused."""
    plain = _as_written(written)
    if real == plain:
        return ""
    if os.name == "nt" and real.startswith("\\\\") and re.match(r"^[a-z]:\\", plain) and real.endswith(plain[2:].rstrip("\\")):
        return _written(real)[0]
    return LINK


# -- the marker ----------------------------------------------------------------------------------------------

def bot_key(state: Path | str) -> str:
    """This bot, in the marker: no name, no path."""
    return hashlib.sha256(canon(state).encode("utf-8")).hexdigest()[:16]


def marker_bot(store: Path | str) -> str:
    """The bot key in the store's marker, "" when absent or unreadable. A link is not a marker."""
    path = Path(store) / fetched.MARKER
    if is_link(path):
        return ""
    try:
        with open(path, "rb") as fh:
            got = json.loads(fh.read(4097).decode("utf-8")).get("bot")
    except _UNREADABLE:
        return ""
    return got if isinstance(got, str) else ""


def _marker_line(state: Path, store: str) -> str:
    if not os.path.lexists(os.path.join(store, fetched.MARKER)):
        return ""
    got = marker_bot(store)
    if got == bot_key(state):
        return ""
    return OTHER_BOT if got else MARKER_JUNK


def write_marker(state: Path | str, store: Path | str) -> None:
    """Say this store is this bot's, atomically. Only an absent marker is written; this bot's own stays as it is (a
    write checks this every time, and a reader may have it open). Anything else raises ``PermissionError``, an
    OSError so a download or a move stops the way it does for any disk problem. The line is ``str()``."""
    state, store = Path(state), Path(os.path.abspath(store))
    line = _way_problem(str(store)) or _marker_line(state, str(store))
    if line:
        raise PermissionError(line)
    path = store / fetched.MARKER
    if os.path.lexists(path):
        return
    store.mkdir(parents=True, exist_ok=True)
    tmp = store / f"{fetched.MARKER}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        tmp.write_text(json.dumps({"bot": bot_key(state)}), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# -- places --------------------------------------------------------------------------------------------------

def _vaults(state: Path) -> list[Path]:
    """Every vault place of a bot, or raises. Stricter than :func:`folders._vault_places` alone: settings of another
    shape, or a place that is not a whole path, would drop a real place without a word."""
    path = state / "settings" / "vault.json"
    if path.exists():
        got = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(got, dict):
            raise ValueError("vault.json is not an object")
        if got.get("folder") is not None and not isinstance(got["folder"], (str, list)):
            raise ValueError("a vault place in settings is not a path")      # 0 and false read as no place at all
    places = _vault_places(state)
    if not all(os.path.isabs(os.path.expandvars(str(p))) for p in places):
        raise ValueError("a vault place is not a whole path")
    return places


def _folders_of(bot: Path) -> tuple[str, list[str], str]:
    """``(data folder, linked folders, knowledge folder chosen or "")`` from a bot's settings. Junk raises."""
    from ..zone3.toolsmith_zone import _folders
    path = bot / "settings" / "folders.json"
    data, linked = _folders(str(path), str(bot))
    chosen = json.loads(path.read_text(encoding="utf-8")).get("knowledge") if path.exists() else None
    if chosen is not None and (not isinstance(chosen, str) or not os.path.isabs(os.path.expandvars(chosen))):
        raise ValueError("knowledge is not a whole path")
    return data, linked, chosen or ""


def _subfolders(parent: Path | str) -> list[Path]:
    """The folders right inside *parent*: none when it is not there. Can't be read: raises."""
    out = []
    try:
        with os.scandir(parent) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        out.append(Path(entry.path))
                except OSError:
                    continue
    except (FileNotFoundError, NotADirectoryError):
        return []
    return out


def _bot_shaped(p: Path) -> bool:
    """A bot's own folder, by its look. ``os.path`` on purpose: every drive has folders nobody may read (System
    Volume Information), and one of those must not stop the whole drive being chosen."""
    return os.path.isfile(p / "settings" / "folders.json") or os.path.isfile(p / "index" / "knowledge.db") \
        or (os.path.isdir(p / "vault") and os.path.isdir(p / "settings"))     # a folder named vault alone is hers


def _home() -> str:
    from ..zone3.toolsmith_zone import home_dir
    home = home_dir()
    return home if os.path.isabs(home) else ""


def _other_bot_places(state: Path, own: str, store: str, own_data: str) -> str:
    """Rule (a) and (b): every other bot this computer can name, and what it keeps where."""
    home = _home()
    from ..paths import data_root
    seen = {own}
    try:
        parents = [state.parent, data_root()]
    except RuntimeError:                               # no home folder at all: no other place to name
        parents = [state.parent]
    for parent in parents:
        try:
            bots = _subfolders(parent)
        except OSError:
            return OTHER_UNREADABLE
        for bot in bots:
            try:
                if not (os.path.isdir(bot / "settings") or os.path.isdir(bot / "vault") or _bot_shaped(bot)) \
                        or canon(bot) in seen:
                    continue
                seen.add(canon(bot))
                data, _linked, chosen = _folders_of(bot)
                places = [bot, data, *_vaults(bot)] + ([os.path.join(chosen, STORE)] if chosen else [])
                if not os.path.isabs(os.path.expandvars(data)):
                    raise ValueError("data is not a whole path")
            except _UNREADABLE:
                return OTHER_UNREADABLE
            if any(_overlap(store, _canon_new(p)) for p in places):
                return OTHER_BOT
    if home:
        try:
            for other in _subfolders(os.path.join(home, "PuffPAI Data")):
                c = _canon_new(other)
                if other.name.lower() != state.name.lower() and c != own_data and _overlap(store, c):
                    return OTHER_BOT
        except OSError:
            return OTHER_UNREADABLE
    return ""


def _bot_by_shape(own: str, written: str, *, near: bool) -> str:
    """Rule (c): a folder that looks like a bot's, above the chosen one and, with *near*, at it, right inside it and
    right inside its store."""
    here = Path(written)
    near_ones = [here] if near else []
    try:
        near_ones += (_subfolders(here) + _subfolders(here / STORE)) if near else []
    except OSError:
        return CANT_CHECK
    for p in [*here.parents, *near_ones]:
        if _bot_shaped(p) and canon(p) != own:
            return OTHER_BOT
    return ""


def _code_places() -> list[Path]:
    """BuildBot's own code: the package, and the folder holding ``core``, when it runs from a repo."""
    here = Path(__file__).resolve()
    return [here.parents[1], here.parents[2]] + ([here.parents[3]] if here.parents[2].name.lower() == "core" else [])


def _users_problem(store: str, chosen: str, home: str) -> bool:
    r"""``C:\Users`` itself, and any profile there that is not hers (Public, Default, another user's)."""
    env = os.environ.get
    roots = list(_LITERAL_USERS)
    if env("SystemDrive"):
        roots.append(os.path.join(str(env("SystemDrive")) + os.sep, "Users"))
    if env("PUBLIC"):
        roots.append(os.path.dirname(str(env("PUBLIC")).rstrip("\\/")))
    if home and os.path.basename(os.path.dirname(home.rstrip("\\/"))).lower() == "users":
        roots.append(os.path.dirname(home.rstrip("\\/")))
    mine = _canon_new(home) if home else ""
    for root in {_canon_new(r) for r in roots}:
        if chosen == root:
            return True
        inside = store[len(root) + 1:] if store.startswith(root + os.sep) else ""
        if inside and os.path.join(root, inside.split(os.sep, 1)[0]) != mine:
            return True
    return False


def _system_line(store: str, chosen: str, *, data_ok: bool) -> str:
    """System folders, and her keys, browser profiles and startup items. *data_ok*: this is the data folder itself,
    which is where the store is by default, so being in AppData (the default data folder is) is not held against it."""
    from ..zone3.toolsmith_zone import BROWSER, KEYS, STARTUP, _sensitive
    env = os.environ.get
    home = _home()
    if os.name == "nt":
        roots = [_LITERAL_ROOT] + ([str(env("SystemDrive")) + os.sep] if env("SystemDrive") else [])
        if chosen in {_canon_new(r) for r in roots}:
            return SYSTEM
        rest = os.path.splitdrive(store)[1].lstrip(os.sep)
        if rest.split(os.sep, 1)[0] in _DRIVE_FOLDERS or _users_problem(store, chosen, home):
            return SYSTEM
        places = list(_LITERAL_SYSTEM) + [str(env(n)) for n in ("SystemRoot", "windir", "ProgramFiles",
                                                                "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
                                                                "PUBLIC") if env(n)]
        if not data_ok:
            places += ([os.path.join(home, "AppData")] if home else []) \
                + [str(env(n)) for n in ("APPDATA", "LOCALAPPDATA") if env(n)]
    else:
        if chosen == os.sep:
            return SYSTEM
        places = list(_POSIX_SYSTEM)
    if any(_overlap(store, _canon_new(p)) for p in places):
        return SYSTEM
    secret = [p for p, cat in _sensitive(home) if cat in (KEYS, BROWSER, STARTUP) and os.path.isabs(p)]
    return SECRETS if any(_overlap(store, _canon_new(p)) for p in secret) else ""


# -- the two checks ------------------------------------------------------------------------------------------

def _problem(state: Path, folder: Path | str, data: Path | str | None, linked: list | None) -> str:
    line, written = _written(folder)
    if line:
        return line
    place = os.path.join(written, STORE)
    line = _drive_line(written) or _way_problem(place)
    if line:
        return line
    chosen, store = _canon_new(written), _canon_new(place)
    line = _resolves_elsewhere(chosen, written) or _resolves_elsewhere(store, place)
    if line:
        return line
    try:
        vaults = _vaults(state)
    except _UNREADABLE:
        return VAULT_UNREADABLE
    if any(_overlap(store, _canon_new(v)) for v in vaults):
        return VAULT
    if data is None or linked is None:
        try:
            _data, got, _chosen = _folders_of(state)
        except _UNREADABLE:
            return SETTINGS_UNREADABLE
        data = fetched.data_folder(state) if data is None else data
        linked = got if linked is None else linked
    own, own_data = _canon_new(state), _canon_new(data)
    data_ok = chosen == own_data and (own_data == _canon_new(state / "data") or not _overlap(own_data, own))
    if not data_ok:
        if _overlap(store, own):
            return OWN
        if _overlap(store, own_data):
            return DATA
    if any(_overlap(store, _canon_new(p)) for p in linked):
        return LINKED
    line = _other_bot_places(state, own, store, own_data)
    if line:
        return line
    if any(_overlap(store, _canon_new(p)) for p in _code_places()):
        return CODE
    # Before the look around: a system folder is refused without reading what is in it.
    return _system_line(store, chosen, data_ok=data_ok) or _bot_by_shape(own, written, near=not data_ok) \
        or _marker_line(state, place)


def problem(state: Path | str, folder: Path | str | None, *, data: Path | str | None = None,
            linked: list | None = None) -> str:
    """Why *folder* may not be the chosen parent, one plain line; "" when it may. It need not exist yet, its drive
    must. *data*: this bot's data folder (default: from its settings). *linked*: her linked folders (same)."""
    try:
        return _problem(Path(os.path.abspath(state)), folder, data, linked)
    except Exception:  # noqa: BLE001 - a check that can't finish refuses
        return CANT_CHECK


def still_safe(state: Path | str, store: Path | str) -> str:
    """The check before every write to a chosen folder: a few stats and two small reads, no folder listed, no other
    bot read. No link on the way now, the marker names no other bot, no overlap with this bot's vault. "" when fine.
    The first time for a folder in this process it also runs the whole of :func:`problem`, since the setting may have
    been edited by hand to name a place no choice would have passed."""
    try:
        state = Path(os.path.abspath(state))
        line, written = _written(store)
        if line:
            return line
        line = _drive_line(written) or _way_problem(written) or _marker_line(state, written)
        if line:
            return line
        here = _canon_new(written)
        line = _resolves_elsewhere(here, written)
        if line:
            return line
        try:
            vaults = _vaults(state)
        except _UNREADABLE:
            return VAULT_UNREADABLE
        if any(_overlap(here, _canon_new(v)) for v in vaults):
            return VAULT
        key = (canon(state), here)
        if key not in _passed:
            line = _problem(state, os.path.dirname(written), None, None)
            if line:
                return line
            _passed.add(key)
        return ""
    except Exception:  # noqa: BLE001 - a check that can't finish refuses
        return CANT_CHECK


def source_problem(state: Path | str, store: Path | str) -> str:
    """Why files may not be moved out of *store* (or into it, when it is the default place, which :func:`problem` never
    saw), "" when they may. A link anywhere on the way, or an overlap with this bot's vault, would empty or fill the
    wrong folder. A drive that is gone is fine here: nothing there can be reached, and the mover says so itself."""
    try:
        state, written = Path(os.path.abspath(state)), os.path.abspath(os.fspath(store))
        if _drive_line(written):
            return ""
        line = _way_problem(written)
        if line:
            return SOURCE_LINK if line == LINK else line
        here = _canon_new(written)
        if _resolves_elsewhere(here, written):
            return SOURCE_LINK
        try:
            vaults = _vaults(state)
        except _UNREADABLE:
            return VAULT_UNREADABLE
        return SOURCE_VAULT if any(_overlap(here, _canon_new(v)) for v in vaults) else ""
    except Exception:  # noqa: BLE001 - a check that can't finish refuses
        return CANT_CHECK


__all__ = ["STORE", "bot_key", "inside_problem", "is_link", "marker_bot", "problem", "source_problem", "still_safe",
           "write_marker"]
