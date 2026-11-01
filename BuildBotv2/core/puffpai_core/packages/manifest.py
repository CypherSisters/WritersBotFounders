"""Load and check ``puffpai.json`` against ``contracts/puffpai-package.schema.json``.

Every problem comes back as one plain sentence, with where it is
(``run.windows.start[0]: ...``). The schema's ``x-plain`` notes are the
messages for its own rules; this module only adds the "where".

Interface (lane L0; ``puffpai check`` Level 1 item 1 and the Package Manager use it):
- ``load(folder, adopted=False) -> (manifest, path)``: reads ``<folder>/puffpai.json``,
  or ``<folder>/.buildbot/puffpai.json`` for a linked folder (02 "Install in place").
  For an adopted app (``adopted=True``: its Registry origin is ``adopted:<url>``) only
  ``.buildbot/puffpai.json`` counts: the Core writes it when Toolsmith hands the try over
  (``toolsmith_host.trials``). A ``puffpai.json`` at the top of a downloaded project is the
  project's own file (or anyone's), never the manifest the Core runs.
  Raises :class:`ManifestError` (``.problems`` is the list) if it is missing,
  not JSON, or not valid. ``load_with_sha`` also returns the sha256 of the bytes it read.
- ``installed(row, folder) -> manifest``: THE one way the Core reads an INSTALLED package's manifest
  (lane I2a): the file the Registry row's origin says, in the folder the Core installed it in, and only
  while its bytes still have the sha256 recorded at her install Yes (``manifest_sha``). Else
  :class:`NotAsApproved` (or :class:`ManifestError`). ``read_installed(registry, installs, id, broken)``
  is the same with "nothing" for an answer: None, and *broken(id, reason)* when the file changed.
  Nothing else in the Core opens an installed package's ``puffpai.json`` itself.
- ``validate(manifest) -> [problem]``: schema problems; empty = valid.
- ``check_files(folder, manifest) -> [problem]``: the files the manifest names,
  or its kind must contain, are there. (Not yet: opening the .glb, parsing
  SKILL.md, checking ``sources`` hashes.) An SVG avatar or look picture is a plain drawing (``svg_problem``).
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from ..paths import contracts_dir

MANIFEST = "puffpai.json"
LINKED_MANIFEST = Path(".buildbot") / MANIFEST

# Validators whose generic message is clearer than a nearby x-plain note.
_GENERIC_FIRST = {"required", "additionalProperties", "type", "unevaluatedProperties"}


class ManifestError(ValueError):
    """``puffpai.json`` can't be used. ``problems`` holds plain sentences."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


#: Said when an installed package's manifest file is not the one she said Yes to (lane I2a part D).
MANIFEST_CHANGED = "Its settings file changed since you said Yes. Add it again to see what changed."
MANIFEST_UNRECORDED = "PuffPAI has no record of the settings file you said Yes to. Add it again."


class NotAsApproved(ManifestError):
    """An installed package's manifest file no longer has the sha256 recorded at her install Yes."""


@functools.lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads((contracts_dir() / "puffpai-package.schema.json").read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(schema())


def _where(path: Any) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or MANIFEST


def _resolve(ref: str) -> dict | None:
    if not ref.startswith("#/"):
        return None
    node: Any = schema()
    for key in ref[2:].split("/"):
        node = node.get(key) if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def _nearest_plain(schema_path: Any) -> str | None:
    """The deepest ``x-plain`` note on the way to the failed rule (root note skipped)."""
    node: Any = schema()
    found = None
    for key in schema_path:
        if isinstance(node, dict) and "$ref" in node and key not in node:
            node = _resolve(node["$ref"])
        if key == "$ref" and isinstance(node, dict):
            node = _resolve(node["$ref"])
            if isinstance(node, dict) and "x-plain" in node:
                found = node["x-plain"]
            continue
        try:
            node = node[key]
        except (KeyError, IndexError, TypeError):
            break
        if isinstance(node, dict) and "x-plain" in node:
            found = node["x-plain"]
    return found


def _plain(error: ValidationError) -> str:
    where = _where(error.absolute_path)
    if error.schema is False:                   # a field the rules forbid here
        note = _nearest_plain(list(error.absolute_schema_path)[:-1])
        return f"{where} is not allowed here." + (f" {note}" if note else "")
    own = error.schema.get("x-plain") if isinstance(error.schema, dict) else None
    if own:
        return f"{where}: {own}" if error.absolute_path else own
    if error.validator in _GENERIC_FIRST:
        if error.validator == "required":
            missing = error.message.split("'")[1] if "'" in error.message else error.message
            return f"{where} needs '{missing}'."
        if error.validator in ("additionalProperties", "unevaluatedProperties"):
            known = set(error.schema.get("properties", {})) if isinstance(error.schema, dict) else set()
            extra = sorted(k for k in error.instance if k not in known) if isinstance(error.instance, dict) else []
            names = ", ".join(f"'{k}'" for k in extra) or "a field"
            return f"{where} has a field the contract does not know: {names}. Check the spelling, or remove it."
        if error.validator == "type":
            return f"{where} must be {error.validator_value}."
    note = _nearest_plain(error.absolute_schema_path)
    if note:
        return f"{where}: {note}" if error.absolute_path else note
    return f"{where}: {error.message}"


#: Package ids no package may take (D79). ``puffpai`` is the Desktop's own name, ``puffpai.localhost``; a
#: package with that id would get the page origin ``puffpai.localhost:<pages port>``, the Desktop's cookie name.
RESERVED_IDS = frozenset({"puffpai"})
RESERVED_ID_LINE = "The id 'puffpai' is BuildBot's own (the Desktop's address), so no package may use it. Pick another id."


def validate(manifest: Any) -> list[str]:
    """Schema problems in plain words, in file order; empty means valid."""
    if not isinstance(manifest, dict):
        return [f"{MANIFEST} must be one JSON object {{...}}."]
    errors = sorted(_validator().iter_errors(manifest), key=lambda e: (list(map(str, e.absolute_path)), e.validator))
    out: list[str] = []
    for e in errors:
        msg = _plain(e)
        if msg not in out:
            out.append(msg)
    if manifest.get("id") in RESERVED_IDS:
        out.append(RESERVED_ID_LINE)
    if not out and manifest.get("kind") == "look":
        from ..looks import manifest_problems
        out += manifest_problems(manifest)        # values only: nothing that loads anything (D76)
    return out


def adopted_origin(origin: Any) -> bool:
    """An ``origin`` (a Registry row's or a manifest's) that says the app was adopted from outside."""
    return isinstance(origin, str) and origin.startswith("adopted:")


def find(folder: Path | str, adopted: bool = False) -> Path | None:
    """The manifest file for a package folder, or None.

    *adopted*: only ``.buildbot/puffpai.json`` (the one the Core wrote) counts, never a top-level one."""
    folder = Path(folder)
    if adopted:
        if folder.is_file():
            folder = folder.parent.parent if folder.parent.name == LINKED_MANIFEST.parent.name else folder.parent
        candidate = folder / LINKED_MANIFEST
        return candidate if candidate.is_file() else None
    if folder.is_file() and folder.name == MANIFEST:
        return folder
    # Where the origin is not known (the Desktop, puffpai check): the Core's file for an adopted app wins
    # over the project's own top-level one.
    linked = folder / LINKED_MANIFEST
    if linked.is_file():
        try:
            if adopted_origin(json.loads(linked.read_text(encoding="utf-8-sig")).get("origin")):
                return linked
        except (OSError, ValueError, AttributeError):
            pass
    for candidate in (folder / MANIFEST, folder / LINKED_MANIFEST):
        if candidate.is_file():
            return candidate
    return None


def load(folder: Path | str, adopted: bool = False) -> tuple[dict, Path]:
    """Read and validate a package's manifest. Raises :class:`ManifestError`.

    *adopted*: see :func:`find`; the manifest must also say ``origin: adopted:...``."""
    manifest, path, _sha = load_with_sha(folder, adopted=adopted)
    return manifest, path


def load_with_sha(folder: Path | str, adopted: bool = False) -> tuple[dict, Path, str]:
    """:func:`load`, and the sha256 of the very bytes it read (the Registry's ``manifest_sha``, recorded at
    the install Yes; Steward runs a package only while its file still has it)."""
    path = find(folder, adopted=adopted)
    if path is None:
        if adopted:
            raise ManifestError([f"No {LINKED_MANIFEST.as_posix()} in {folder}: an adopted app runs only from the "
                                 f"{MANIFEST} the Core wrote."])
        raise ManifestError([f"No {MANIFEST} in {folder} (looked in the folder and in its .buildbot folder)."])
    try:
        raw = path.read_bytes()
    except OSError:
        raise ManifestError([f"{MANIFEST} could not be read."]) from None
    sha = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError:
        raise ManifestError([f"{MANIFEST} is not UTF-8 text."]) from None
    except json.JSONDecodeError as exc:
        raise ManifestError([f"{MANIFEST} is not valid JSON: line {exc.lineno}, column {exc.colno}: {exc.msg}."]) from None
    problems = validate(manifest)
    if problems:
        raise ManifestError(problems)
    if adopted and not adopted_origin(manifest.get("origin")):
        raise ManifestError([f"{LINKED_MANIFEST.as_posix()} does not say the app was adopted."])
    return manifest, path, sha


def installed(row: Any, folder: Path | str | None) -> dict:
    """The manifest of an INSTALLED package: the one way the Core reads it (lane I2a).

    *row* is its Registry row, *folder* the folder the Core installed it in (``Installs.folder``). The file
    is the one the row's origin says (an adopted app: only ``.buildbot/puffpai.json``, never a top-level
    one the download shipped), and it counts only while the very bytes read have the sha256 recorded in
    the row at her install Yes (``manifest_sha``). A row with no sha counts as changed.
    Raises :class:`NotAsApproved` (changed) or :class:`ManifestError` (missing, unreadable, not valid)."""
    if not isinstance(row, dict) or not row.get("id"):
        raise ManifestError(["It is not installed."])
    if folder is None or not Path(folder).is_dir():
        raise ManifestError([f"The files of {row['id']!r} are not where they were installed."])
    data, _path, sha = load_with_sha(folder, adopted=adopted_origin(row.get("origin")))
    want = row.get("manifest_sha")
    if not isinstance(want, str) or not want:
        raise NotAsApproved([MANIFEST_UNRECORDED])
    if not hmac.compare_digest(sha, want):
        raise NotAsApproved([MANIFEST_CHANGED])
    return data


def read_installed(registry: Any, installs: Any, id: str,
                   broken: Optional[Callable[[str, str], None]] = None) -> Optional[dict]:
    """:func:`installed` for a reader that only looks: the manifest, or None. When the file is not the one
    she said Yes to, *broken(id, reason)* is called too (the row is treated as broken), then None."""
    row = registry.get(id) if registry is not None and isinstance(id, str) and id else None
    if row is None or installs is None:
        return None
    try:
        return installed(row, installs.folder(id))
    except NotAsApproved as exc:
        if broken is not None:
            broken(id, exc.problems[0] if exc.problems else MANIFEST_CHANGED)
        return None
    except (ManifestError, OSError, ValueError, TypeError):
        return None


def check_files(folder: Path | str, manifest: dict) -> list[str]:
    """Files the manifest names, or its kind must contain, that are missing."""
    # Paths are relative to the package folder, also for a linked folder whose
    # manifest sits in .buildbot\.
    root = Path(folder)
    if root.is_file():
        root = root.parent
    wanted: list[tuple[str, str]] = []          # (relative path, why)

    if manifest.get("kind") == "skill":
        wanted.append(("SKILL.md", "a skill needs SKILL.md"))
    if "icon" in manifest:
        wanted.append((manifest["icon"], "icon"))
    avatar = manifest.get("avatar") or {}
    if "model" in avatar:
        if not str(avatar["model"]).lower().endswith(".glb"):
            return [f"avatar.model must be a .glb file, not {avatar['model']!r}."]
        wanted.append((avatar["model"], "avatar.model"))
    if "preview" in avatar:
        wanted.append((avatar["preview"], "avatar.preview"))
    look = manifest.get("look") if isinstance(manifest.get("look"), dict) else {}
    if "preview" in look:
        wanted.append((look["preview"], "look.preview"))
    if "labels" in manifest:
        wanted.append((manifest["labels"], "labels"))
    for os_name, block in (manifest.get("run") or {}).items():
        if block.get("type") == "docker_compose":
            wanted.append((block["file"], f"run.{os_name}.file"))
        elif block.get("type") == "static":
            wanted.append((block["dir"], f"run.{os_name}.dir"))
    ui = manifest.get("ui") or {}
    for i, page in enumerate(ui.get("pages", [])):
        if "file" in page:
            wanted.append((page["file"], f"ui.pages[{i}].file"))
    for i, widget in enumerate(ui.get("widgets", [])):
        if "file" in widget:
            wanted.append((widget["file"], f"ui.widgets[{i}].file"))
    for i, item in enumerate(ui.get("tray", [])):
        if "icon" in item:
            wanted.append((item["icon"], f"ui.tray[{i}].icon"))
    for i, rel in enumerate(manifest.get("files", [])):
        wanted.append((rel, f"files[{i}]"))
    for i, step in enumerate(manifest.get("steps", [])):
        if "page" in step:
            wanted.append((step["page"], f"steps[{i}].page"))

    problems = []
    for rel, why in wanted:
        target = root / rel
        if why.endswith(".dir"):
            if not target.is_dir():
                problems.append(f"{why}: the folder {rel!r} is not in the package.")
        elif not target.is_file():
            problems.append(f"{why}: the file {rel!r} is not in the package.")
        elif why in ("avatar.preview", "look.preview") and str(rel).lower().endswith(".svg"):
            bad = svg_problem(target)
            if bad:
                problems.append(f"{why}: {bad}")
    return problems


#: What a face or look picture (an SVG) may not have: script, event handlers, outside links or embedded pages.
_SVG_BAD = re.compile(r"<\s*script|<\s*foreignObject|<\s*iframe|<\s*object|<\s*embed|\son[a-z]+\s*=|javascript:"
                      r"|(?:xlink:)?href\s*=\s*[\"'](?!#)|url\(\s*[\"']?(?!#)|@import|<!ENTITY", re.IGNORECASE)
SVG_MAX = 512 * 1024


def svg_problem(path: Path) -> str | None:
    """Why the SVG at *path* can't be a picture here, or None: a plain drawing only (D76)."""
    try:
        if path.stat().st_size > SVG_MAX:
            return f"the picture is over {SVG_MAX // 1024} KB."
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return "the picture could not be read as text."
    found = _SVG_BAD.search(text)
    if found:
        return f"the picture has {found.group(0).strip()!r}; a picture is a plain drawing, with no script or outside link."
    return None
