# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/cli.py (_cmd_build), src/puffpai/build_bundle.py (rewritten-from)
r"""Move a finished bot to another computer (D141): export it to a folder that can go in a git repo, and import it there.

v1's rule stays: a bundle carries the build's decisions, not model files, and import checks every file's hash and
refuses the whole bundle on one bad one. No network at any point, either way.

**It travels:** the bot's name and look, who your bot is, what opens first, its Home layout, which apps and their
versions, the code of what Toolsmith built or you added from a folder, and, by name only, the books, linked folders,
pinned documents and added documents (a copy in ``uploads`` goes by name only if it is pinned).

**They come back from a list (D148):** :func:`apply` keeps what came as ``settings\moved_list.json``, and Knowledge shows it
as "Recommended for this bot" (:func:`recommended`). She picks. Each row goes by the normal path and its normal Yes, and
nothing downloads on its own.

**They come back from a list (D148):** :func:`apply` keeps what came as ``settings\moved_list.json``, and Knowledge shows it
as "Recommended for this bot" (:func:`recommended`). She picks. Each row goes by the normal path and its normal Yes, and
nothing downloads on its own. What the builds read from Wikipedia and Gutenberg travels as its topics, by name
(``knowledge.topics``, :func:`reading_topics`), and comes back as one plan with one Yes.

**It never travels** (:data:`STAYS`, said on the screen): the vault and everything in it, chats, paired devices, app data,
People and You, the Bridge's Yes answers, and any setting that loosens a check. Those are entered again on the new
computer, so each app there asks its own Yes as on any install.

The scan (:func:`private_in`) is what the Core already has: ``zone1.classify`` and ``zone1.secret_detector``. A private
detail in the bot's own record stops the export with nothing written. One in an app's code leaves that app out, and says
so. A file that can't be read as text and isn't a plain picture or font is left out, since it can't be scanned.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import bot, jsonfile, layout, opens
from .packages import fsutil
from .zone1 import fetched
from .zone1.folders import _inside, _vault_places, added_paths, canon, pinned_paths

SCHEMA = "puffpai-bot/1"
MAIN = "puffpai-bot.json"
SUMS = "checksums.sha256"
APPS = "apps"
README = "README.md"
#: Setup asks the rest: the brain, the vault and its PIN, the Bridge, Toolsmith's model.
LIST = "moved_list.json"
SETUP_ALREADY = ("goal", "name", "look", "plan", "yes", "build", "prove")
#: The most of one app carried, so a folder stays fit for a git repo. A bigger one is named, for her to find again.
APP_MAX = 100 * 1024 * 1024
TEXT_MAX = 8 * 1024 * 1024
_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_KEY_WORD = re.compile(r"[A-Za-z0-9_+/=-]+")
#: Words about a subject, not details about a person: an app's own text is full of them.
_NOT_IN_CODE = ("health.", "finance.topic")
_PLAIN_BINARY = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".glb"})
#: Never carried, whatever is in them: these hold what someone typed or what a program saved.
_NEVER_NAMES = frozenset({".env", ".netrc", "id_rsa", "id_ed25519"})
_NEVER_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".pfx", ".p12", ".bak", ".dump"})
#: Part of BuildBot itself: setup adds them.
_SYSTEM = frozenset({"desktop", "wizard", "cloud-avatar"})
STAYS = (
    "Your vault and everything in it: keys, passwords and private details.",
    "Your chats.",
    "Paired devices and phones.",
    "What is inside your apps. Each app starts empty there.",
    "People and You.",
    "The documents themselves. Linked folders, pinned documents and documents you added go by name only, to link, pin and add again. Other copies sent from a device aren't named, so send those again.",
    "Books and model files. Books come back from a list on Knowledge, and the brain is picked again in setup.",
    "Your Yes answers: the Bridge, Drawbridge's setting, and changing your documents start as a new bot does, and each app asks its own Yes.",
    "Tool servers and APIs you added, with their keys. They go by name only, to add again.",
)
FOOTER = "PuffPAI by Cypher Sisters. puff puff pass"


class MoveRefused(Exception):
    """A plain line for her: nothing was written or changed."""


def private_in(text: str) -> list[str]:
    """What the Core's own detectors would hold in *text*, by kind. A check that breaks counts as a find."""
    from .zone1 import labels, secret_detector
    from .zone1.classify import classify_text
    try:
        found = set()
        for h in classify_text(text, path="outbound").hits:
            secret = labels.is_secret(h.label)
            if secret or (not h.maybe and labels.effects(h.label).get("hold")):
                found.add(h.label)
        if secret_detector.find(text):
            found.add("secret.token")
    except Exception:  # noqa: BLE001 - unchecked is not clean
        return ["something that could not be checked"]
    return sorted(found)


def _key_shaped(value: str) -> bool:
    """A value someone could use as a key: one long word of letters and digits, not code that names a key."""
    v = value.strip().strip("\"'`")
    return len(v) >= 10 and bool(_KEY_WORD.fullmatch(v)) and any(c.isdigit() for c in v) and any(c.isalpha() for c in v)


def private_in_code(text: str) -> list[str]:
    """The same detectors, for an app's own files. They read prose, so ``token or ""`` reads as a key and the word
    "medicine" as a health detail. Here a key counts only with a value shaped like one, and a detail only if it is one."""
    from .zone1 import labels
    from .zone1.classify import classify_text
    try:
        found = set()
        for h in classify_text(text, path="outbound").hits:
            if h.label == "secret.api_key" or ((labels.is_secret(h.label) or h.label.startswith("auth.")) and _key_shaped(text[h.start:h.end])):
                found.add(h.label)
            elif not labels.is_secret(h.label) and not h.maybe and labels.effects(h.label).get("hold") and not h.label.startswith(_NOT_IN_CODE):
                found.add(h.label)
    except Exception:  # noqa: BLE001 - unchecked is not clean
        return ["something that could not be checked"]
    return sorted(found)


def _kinds(found: list[str]) -> str:
    """In words the scan itself won't read as a secret."""
    words = ["a key or a password" if x.startswith(("secret", "auth")) else x.replace(".", " ") for x in found[:4]]
    return ", ".join(dict.fromkeys(words))


def _target_problem(state: Path, folder: Path) -> str:
    if not folder.is_absolute():
        return "Use the folder's whole path."
    real = canon(folder)
    try:
        places = [state, *_vault_places(state), fetched.data_folder(state)]
    except (OSError, ValueError, TypeError):
        return "BuildBot can't read where your vault is kept, so nothing was written."
    if any(_inside(real, canon(p)) or _inside(canon(p), real) for p in places):
        return "That folder is part of this bot, or holds it. Pick another one."
    if folder.is_file():
        return "That is a file. Pick a folder."
    if folder.is_dir() and not (folder / MAIN).exists():
        left = [p.name for p in folder.iterdir() if p.name not in (".git", ".gitattributes", ".gitignore", README, "LICENSE")]
        if left:
            return "That folder has other things in it. Pick an empty folder, or an empty git repo."
    return ""


def _carry(src: Path) -> tuple[list[tuple[str, bytes]], list[str], str]:
    """``(files, what was left out, why the whole app stays)``. Links and :data:`fsutil.SKIP_NAMES` never go."""
    files, left, total = [], [], 0
    for here, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if not fsutil.is_skip(d) and not fsutil.is_link(Path(here) / d)]
        for name in names:
            p = Path(here) / name
            rel = p.relative_to(src).as_posix()
            if p.relative_to(src) in fsutil.CORE_WRITTEN:
                continue                               # the Core writes it at each start: a proxy port and a credential
            suffix = p.suffix.lower()
            if fsutil.is_link(p) or name.lower() in _NEVER_NAMES or suffix in _NEVER_SUFFIXES:
                left.append(rel)
                continue
            try:
                data = p.read_bytes()
            except OSError:
                left.append(rel)
                continue
            total += len(data)
            if total > APP_MAX:
                return [], left, "it is too big for a folder like this"
            if suffix in _PLAIN_BINARY:
                files.append((rel, data))
                continue
            if b"\x00" in data[:8192] or len(data) > TEXT_MAX:
                left.append(rel)
                continue
            found = private_in_code(data.decode("utf-8", "replace"))
            if found:
                return [], left, f"{rel} holds {_kinds(found)}"
            files.append((rel, data))
    return files, left, ""


def _names(paths: list[Any]) -> list[str]:
    return sorted({Path(p).name for p in paths})


def _app_rows(state: Path, registry: Any, packages: Any) -> tuple[list[dict], list[dict], dict[str, list]]:
    """``(apps, books, what each carried app's files are)``."""
    apps, books, carried = [], [], {}
    for row in registry.list():
        pid = str(row.get("id") or "")
        if row.get("status") == "removed" or pid in _SYSTEM or not _ID.match(pid):
            continue
        name, origin = str(row.get("name") or pid), str(row.get("origin") or "")
        if row.get("kind") == "knowledge":
            book = {"source": pid, "title": name}
            if origin.startswith(("store:", "adopted:")):
                book["from"] = "store" if origin.startswith("store:") else "download"
            books.append(book)
            continue
        out = {"id": pid, "name": name, "kind": row.get("kind"), "version": str(row.get("version") or "")}
        if packages.builtin_of(pid) is not None:
            out["source"] = pid
        elif origin.startswith("store:"):
            out["source"] = f"store:{pid}"
            out["commit"] = str((row.get("store") or {}).get("commit") or "")
        elif origin.startswith("github:"):
            out["source"] = origin[len("github:"):]
        else:
            where = packages.installs.folder(pid)
            files, left, why = _carry(Path(where)) if where is not None and Path(where).is_dir() else ([], [], "its files are not there")
            if why:
                out["left_out"] = why
                if origin.startswith("adopted:"):
                    out["from"] = origin[len("adopted:"):]
            else:
                out["source"] = f"{APPS}/{pid}"
                if left:
                    out["files_left_out"] = left[:40]
                carried[pid] = files
        apps.append(out)
    return apps, books, carried


def _settings(state: Path, registry: Any) -> dict:
    from .zone2 import persona
    who = persona.load(state)
    said = bot.read(state, registry)
    return {"bot": {"name": said.get("name"), "theme": said.get("theme")},
            "persona": {"text": who.get("text")} if who.get("text") else None,
            "opens": opens.read(state),
            "layout": jsonfile.read(layout.path_for(state), {}),
            "theme": jsonfile.read(Path(state) / "settings" / "theme.json", {})}


def _plan_topics(state: Path) -> list[str]:
    """The topics that came with this bot, then those its builds read something on, oldest first, each once. A bot that
    never read any has none."""
    topics = reading_topics(state)
    rows = []
    for path in (state / "builds").glob("*/build.json"):
        got = jsonfile.read(path, {})
        rows.append(got if isinstance(got, dict) else {})
    for b in sorted(rows, key=lambda x: str(x.get("created_at") or "")):
        run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
        if any(isinstance(n, int) and n > 0 for n in (run.get("counts") or {}).values()):
            topics += [str(t) for t in (b.get("knowledge_plan") or {}).get("topics") or []]
    return list({t.lower(): t for t in reversed(topics)}.values())[::-1]


def _added_names(state: Path) -> list[str]:
    """Tool servers and APIs she added, by name only: never an address, and never a key."""
    out = []
    for name in ("tool_servers.json", "owner_apis.json"):
        got = jsonfile.read(Path(state) / "settings" / name, {})
        rows = got if isinstance(got, list) else list(got.values()) if isinstance(got, dict) else []
        for row in rows:
            for one in (row if isinstance(row, list) else list(row.values()) if isinstance(row, dict) and "name" not in row else [row]):
                if isinstance(one, dict) and isinstance(one.get("name"), str):
                    out.append(one["name"][:80])
    return sorted(set(out))


def export(state: Path | str, registry: Any, packages: Any, folder: Path | str) -> dict:
    """Scan everything, then write. ``{folder, apps, left_out, stays}``, or :class:`MoveRefused` with nothing written."""
    from .zone1.folders import Library
    state, folder = Path(state), Path(os.path.expanduser(str(folder)))
    line = _target_problem(state, folder)
    if line:
        raise MoveRefused(line)
    settings = _settings(state, registry)
    apps, books, carried = _app_rows(state, registry, packages)
    record = {"schema": SCHEMA, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **settings,
              "apps": apps,
              "knowledge": {"books": books, "topics": _plan_topics(state), "linked": _names(Library(None, state).linked()),
                            "pinned": _names(pinned_paths(state)), "documents": _names(added_paths(state))},
              "added": _added_names(state), "stays": list(STAYS)}
    body = json.dumps(record, indent=1, ensure_ascii=False, sort_keys=True)
    found = private_in(body)
    if found:
        raise MoveRefused(f"Your bot's own settings hold {_kinds(found)}, so nothing was written. Look at Who your bot is and "
                          "your document and folder names, change it there, and export again.")
    readme = _readme(record)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(folder / APPS, ignore_errors=True)
    written = {MAIN: body.encode("utf-8"), README: readme.encode("utf-8"), ".gitattributes": b"* -text\n"}
    for pid, files in carried.items():
        for rel, data in files:
            written[f"{APPS}/{pid}/{rel}"] = data
    sums = []
    for rel, data in sorted(written.items()):
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        sums.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
    (folder / SUMS).write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    return {"folder": str(folder), "apps": [a["name"] for a in apps if a.get("source")],
            "left_out": [{"name": a["name"], "why": a["left_out"]} for a in apps if a.get("left_out")],
            "stays": list(STAYS)}


def _readme(record: dict) -> str:
    name = (record.get("bot") or {}).get("name") or "A bot"
    lines = [f"# {name}", "", "A PuffPAI bot, moved as a folder. To put it on another computer:", "", "```",
             "./install.sh --from <this folder>", "```", "",
             "On Windows: `install.ps1 -From <this folder>`. Setup asks only what can't travel.", "", "## What doesn't travel", ""]
    lines += [f"- {s}" for s in record["stays"]] + ["", FOOTER, ""]
    return "\n".join(lines)


# -- import ---------------------------------------------------------------------------------------

def read(folder: Path | str) -> dict:
    """Check every file's hash, refuse the whole bundle on one bad one (v1), and read it. Nothing is changed."""
    folder = Path(os.path.expanduser(str(folder)))
    try:
        raw_sums = (folder / SUMS).read_text(encoding="utf-8")
        raw = (folder / MAIN).read_bytes()
    except OSError:
        raise MoveRefused("That folder isn't a bot moved from another computer: it has no puffpai-bot.json.") from None
    listed = {}
    for line in raw_sums.splitlines():
        sha, _, rel = line.partition("  ")
        parts = Path(rel).parts
        if not sha or not rel or ".." in parts or Path(rel).is_absolute() or "\\" in rel or ":" in rel:
            raise MoveRefused("This bundle lists a file outside its own folder, so nothing was used.")
        listed[rel] = sha
    if MAIN not in listed:
        raise MoveRefused("This bundle has no hash for its own record, so nothing was used.")
    for rel, sha in listed.items():
        p = folder / rel
        if not os.path.lexists(p):
            raise MoveRefused(f"{rel} is missing from this bundle, so nothing was used.")
        if fsutil.is_link(p):
            raise MoveRefused(f"{rel} is a link, so nothing was used.")
        try:
            got = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            raise MoveRefused(f"{rel} is missing from this bundle, so nothing was used.") from None
        if got != sha:
            raise MoveRefused(f"{rel} has changed since this bundle was made, so nothing was used.")
    apps_dir = folder / APPS
    if apps_dir.is_dir():
        for here, _dirs, names in os.walk(apps_dir):
            for name in names:
                rel = (Path(here) / name).relative_to(folder).as_posix()
                if rel not in listed:
                    raise MoveRefused(f"{rel} isn't on this bundle's list, so nothing was used.")
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise MoveRefused("This bundle's record can't be read, so nothing was used.") from None
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise MoveRefused("This bundle is from a version of BuildBot this one can't read, so nothing was used.")
    found = [x for x in private_in(json.dumps(record, ensure_ascii=False)) if x.startswith("secret")]
    if found:
        raise MoveRefused(f"This bundle holds {_kinds(found)}, so nothing was used.")
    for a in record.get("apps") or []:
        src = str(a.get("source") or "") if isinstance(a, dict) else ""
        if src.startswith(f"{APPS}/") and (not _ID.match(src[len(APPS) + 1:]) or not (folder / src).is_dir()):
            raise MoveRefused("This bundle names an app that isn't in it, so nothing was used.")
    record["_folder"] = str(folder)
    return record


def _runs_here(record: dict, app: dict) -> bool:
    """A carried app with a ``run`` block for this kind of computer. One proved on another won't run here until it is
    tried here. Anything else (a built-in, the store, a git address, no block at all) answers for itself at add."""
    from .steward.steward import run_block
    src = str(app.get("source") or "")
    if not src.startswith(f"{APPS}/"):
        return True
    for name in ("puffpai.json", ".buildbot/puffpai.json"):
        try:
            got = json.loads((Path(record["_folder"]) / src / name).read_text(encoding="utf-8"))
        except (OSError, ValueError, KeyError):
            continue
        run = got.get("run") if isinstance(got, dict) else None
        return not isinstance(run, dict) or run_block(got) is not None
    return True


def to_add(record: dict) -> list[dict]:
    """The apps to add on this computer."""
    return [a for a in record.get("apps") or [] if isinstance(a, dict) and a.get("source") and _runs_here(record, a)]


def lines_for(record: dict) -> dict:
    """What she is told before and after: ``{name, apps, not_here, books, linked, pinned, documents, added, left_out, stays}``."""
    know = record.get("knowledge") if isinstance(record.get("knowledge"), dict) else {}
    apps = [a for a in record.get("apps") or [] if isinstance(a, dict)]
    here = [a for a in apps if a.get("source") and _runs_here(record, a)]
    documents = know.get("documents")                  # a bundle from before there was such a list has none
    return {"name": str((record.get("bot") or {}).get("name") or ""),
            "topics": _clean_topics(know.get("topics")),
            "apps": [{"id": a.get("id"), "name": a.get("name"), "version": a.get("version"), "source": a.get("source")} for a in here],
            "not_here": [str(a.get("name")) for a in apps if a.get("source") and a not in here],
            "left_out": [{"name": a.get("name"), "why": a.get("left_out"), "from": a.get("from")} for a in apps if a.get("left_out")],
            "books": [str(b.get("title")) for b in know.get("books") or [] if isinstance(b, dict)],
            "linked": [str(x) for x in know.get("linked") or []], "pinned": [str(x) for x in know.get("pinned") or []],
            "documents": [str(x) for x in documents] if isinstance(documents, list) else [],
            "added": [str(x) for x in record.get("added") or []], "stays": list(STAYS)}


def apply(state: Path | str, record: dict, registry: Any = None) -> list[str]:
    """Her name, look, who the bot is, what opens first, its Home. A look that comes with an app waits for
    :func:`finish_look`. Returns plain lines for anything that didn't take."""
    from .zone2 import persona
    state = Path(state)
    said = []
    b = record.get("bot") if isinstance(record.get("bot"), dict) else {}
    try:
        bot.save(state, name=b.get("name"), by="owner", registry=registry)
    except ValueError as exc:
        said.append(f"The name did not come across: {exc}")
    finish_look(state, record, registry, said)
    who = record.get("persona")
    if isinstance(who, dict) and isinstance(who.get("text"), str) and not persona.control_problem(who["text"]):
        persona.save(state, {"text": who["text"], "source": "owner", "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    try:
        opens.save(state, record.get("opens"))
    except ValueError:
        pass
    if isinstance(record.get("layout"), dict):
        jsonfile.write(layout.path_for(state), record["layout"])
    if isinstance(record.get("theme"), dict) and record["theme"]:
        jsonfile.write(state / "settings" / "theme.json", record["theme"])
    know = record.get("knowledge") if isinstance(record.get("knowledge"), dict) else {}
    jsonfile.write(state / "settings" / LIST, {"rows": _list_rows(record), "topics": _clean_topics(know.get("topics"))})
    return said


def _list_rows(record: dict) -> list[dict]:
    """``[{id, name, kind, get, source?, line?}]``. ``get`` says how it comes back: ``add`` is the Package Manager's own card,
    ``find`` is Find books' plan card by its title, and ``ask`` is a line, for what only Toolsmith can look for."""
    here = to_add(record)
    rows = []
    for a in record.get("apps") or []:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            continue
        row = {"id": a["id"], "name": str(a.get("name") or a["id"]), "kind": "app", "get": "ask"}
        if a in here:
            row.update(get="add", source=app_source(record, a))
        elif a.get("source"):
            row["line"] = "It was set up on another kind of computer. Ask Toolsmith to try it here."
        elif a.get("left_out"):
            row["line"] = f"It did not come: {a['left_out']}. Ask Toolsmith for it."
        else:
            continue
        rows.append(row)
    know = record.get("knowledge") if isinstance(record.get("knowledge"), dict) else {}
    for b in know.get("books") or []:
        if not isinstance(b, dict) or not isinstance(b.get("source"), str) or not _ID.match(b["source"]):
            continue
        row = {"id": b["source"], "name": str(b.get("title") or b["source"]), "kind": "book", "get": "ask"}
        if b.get("from") == "store":
            row.update(get="add", source=f"store:{b['source']}")
        elif b.get("from") == "download":
            row["get"] = "find"
        else:
            row["line"] = "Ask Toolsmith for it."
        rows.append(row)
    return rows


def _clean_topics(raw: Any) -> list[str]:
    return [" ".join(t.split())[:80] for t in raw[:40] if isinstance(t, str) and t.strip()] if isinstance(raw, list) else []


def reading_topics(state: Path | str) -> list[str]:
    """The topics this bot came with, by name, or none: an older bundle, or a bot that never moved."""
    got = jsonfile.read(Path(state) / "settings" / LIST, {})
    return _clean_topics(got.get("topics")) if isinstance(got, dict) else []


def recommended(state: Path | str, registry: Any) -> list[dict]:
    """What the moved bot came with that isn't on this one (D148). It only reads: a row leaves when that thing is here."""
    got = jsonfile.read(Path(state) / "settings" / LIST, {})
    rows = got.get("rows") if isinstance(got, dict) else None
    have = [r for r in registry.list() if r.get("status") != "removed"]
    ids, books = {r.get("id") for r in have}, {r.get("name") for r in have if r.get("kind") == "knowledge"}
    return [r for r in rows or [] if isinstance(r, dict) and r.get("id") not in ids
            and not (r.get("kind") == "book" and r.get("name") in books)]


def finish_look(state: Path | str, record: dict, registry: Any = None, said: list | None = None) -> None:
    theme = (record.get("bot") or {}).get("theme") if isinstance(record.get("bot"), dict) else None
    if not theme:
        return
    try:
        bot.save(state, theme=theme, by="owner", registry=registry)
    except ValueError:
        if said is not None and not any(isinstance(a, dict) and a.get("kind") == "look" for a in record.get("apps") or []):
            said.append("The look did not come across. Pick one in Looks.")


def seed_setup(state: Path | str) -> None:
    """Setup won't ask what came with the bot, or plan a build for it. It still asks what can't travel."""
    path = Path(state) / "settings" / "wizard.json"
    data = jsonfile.read(path, {})
    data = data if isinstance(data, dict) else {}
    steps = data.setdefault("steps", {})
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sid in SETUP_ALREADY:
        steps[sid] = {"state": "done", "at": now, "imported": True}
    jsonfile.write(path, data)


def app_source(record: dict, app: dict) -> str:
    """What ``packages.add`` takes for this app: its own id, ``store:<id>``, a git address, or its folder in the bundle."""
    src = str(app.get("source") or "")
    return str(Path(record["_folder"]) / src) if src.startswith(f"{APPS}/") else src


__all__ = ["MoveRefused", "STAYS", "app_source", "apply", "export", "finish_look", "lines_for", "private_in", "read", "reading_topics",
           "recommended", "seed_setup"]
