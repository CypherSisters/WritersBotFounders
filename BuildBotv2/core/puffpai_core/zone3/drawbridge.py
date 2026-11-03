# Harvested in part from PuffPAIBuildBot@63c8a037:src/puffpai/drawbridge.py (decide, line_for, the block/hold/warn shape; adapted)
# Harvested in part from PuffPAIBuildBot@63c8a037:src/puffpai/integrate/classify.py (_walk, now _entries with nothing skipped; _is_reparse, _TUNNEL_RE, _tunnel_in as-is)
"""Drawbridge: rules-first checks, no model in any decision (01, 09 sections 3 and 6, X25 to X27).

Three checks, each a fixed rule set with closed reason codes:

``check_package(path, manifest, *, origin, tools) -> verdict``
    Before install (the Package Manager calls it, never the checked code).
    **block** (never installed, whatever the owner says): a page that hides
    encoded code and runs it, clearly hostile text in a README wherever it sits
    (the bare words "system prompt" are only hidden, an order aimed at one stops
    it, D97), the same text in a file under ``docs/`` under Locked down, or under
    Normal when hiding its lines does not take it out (D105), owner-read manifest
    text that holds orders to an AI
    (there the bare words still count), a host that breaks the floor
    (an address, a wildcard), ``needs.admin`` on a community provider
    (D31(d)), over 4 GB, a check that crashed, an unknown kind.
    **hold** (her Yes for exactly that): a community provider that reads raw
    values (X13, D28(4)); and anything the checks **can't check** (``cant_check``):
    a file over the read limit, a kind of file it can't read (a binary that is
    not the picture, font or sound its name says, a program, an archive, a key
    file, or a file of a kind no list names that is not plain UTF-8 text), a
    link or reparse point, a file it can't open, or more files than it walks;
    a tunnel tool used in code (ngrok, localtunnel, ``cloudflared tunnel``), in
    both of the owner's settings: the same hold and the same card, her Yes for
    those bytes, every such file listed in the row's ``tunnel`` and counted in the
    verdict's ``tunnels``, and never "eased" (D117: the pattern can't tell whether
    the tunnel is on by default, so the card says "can open", never "opens"; a
    tunnel with any block above is still a block). A file held unread is read for
    a tunnel too, its first bytes only;
    and, under **Normal** (D105), a file under ``docs/`` with that hostile text,
    when hiding its lines takes the text out: the same hold, the same card, her
    Yes for those bytes (:func:`held_fingerprints`), and the lines stay hidden
    from Toolsmith's model before and after it. It counts in ``eased``, so such a
    verdict binds only while the setting is still Normal.
    A file of a kind no list names that IS plain text is read and gets every
    check code gets (D78). **The owner's setting for files it can't read (D95,**
    ``files_mode``): under **Normal** (the default) a picture, sound, video,
    font, pure-data model, clean document, or an archive of those, passes with
    no card when :mod:`.kinds` finds it really is that kind (its first bytes and
    layout, a size cap, no program or zip hidden in it); files that can run code
    (``.jar``, pickles, ``.exe``, ``.wasm``, ``.data`` bundles, macro documents)
    and kinds no list names still hold. Under **Locked down** every file it
    can't read holds, as before D95. The pictures, fonts and sounds it always
    passed by their first bytes get the same disguise check and caps in both
    modes, so Locked down is never looser than Normal. The verdict records the
    setting (``files_mode``) and how many files passed only under Normal
    (``eased``); such a verdict binds only while the setting is still Normal
    (:func:`verify_tree`, :meth:`Drawbridge.verify`). The hold card says which
    files ask and why in plain words (``said``: counts by kind, at most three
    base names a line); every held path stays in the row (``all``) for the
    Core's own use. **warn** (said on the card): hosts the code names
    that ``needs.hosts`` does not list, lines hidden from the model, over 1 GB.
    Never blocks on size under the bound, ``curl | sh``, install lines or a
    missing licence. The verdict is bound to the folder's tree hash: install
    runs only if :func:`verify_tree` still matches (X24). **Nothing is skipped:**
    every file at every depth is scanned and hashed, ``node_modules``,
    ``.venv``, ``venv``, ``__pycache__`` and ``.git`` included (lane L1 removes
    the top-level ones from staging before the check, so nothing legit is left
    to skip; anything still there is checked like any other file).

``classify_tools(manifest, tools, origin) -> {tool: changing|read_only}``
    X27: the Registry's ``abilities.changes`` always counts as changing; a tool
    whose own MCP annotations say ``destructiveHint: true`` or
    ``readOnlyHint: false`` counts as changing even if the manifest leaves it
    out; a name that says it changes things (add, delete, send, ...) counts as
    changing. For adopted and made packages (Toolsmith wrote the manifest), a
    tool with no annotations is **changing until the owner marks it
    read-only**. Annotations only ever add reviews, never remove one.

``Drawbridge.review_action(package, tool, args, labels=...) -> verdict``
    Every ``changes`` call (X27, "Everyday changes"). In memory, no I/O but
    one audit line, budget under 50 ms. Inside what the owner approved:
    ``allow`` with a review id and one audit line, no card. A Yes (``ask``)
    for: a tool she set to ask, a tool outside the approved list, an argument
    outside the declared targets, labelled data to a package with internet
    hosts (X19), a floor-level tool, a tool it can't classify. A secret in the
    arguments is ``deny``. A missing or broken check is ``deny`` at once with
    the plain safe-mode line (X26).

The rules live in this file (``core\\``, read-only at runtime, replaced on
update). Toolsmith and packages hold no token that writes them or the audit
log (X24): no Core API route writes either.

What a scan does not promise (09 section 6): "Drawbridge stops real
code-safety problems; it is not a promise that an outside app is safe, so the
Yes card tells you what an app can do once it runs."
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import hidden_loader, kinds, text_rules
from .chain import ChainLog
from .policy import check_hosts, is_secret_label

ALLOW, WARN, HOLD, BLOCK = "allow", "warn", "hold", "block"
KINDS = frozenset({"app", "tool", "skill", "page", "ui", "avatar", "look", "agent", "knowledge", "bundle", "wizard",
                   "provider"})
DISK_BOUND = 4 * 1024 ** 3
DISK_NOTE = 1024 ** 3
MAX_TEXT_READ = 4 * 1024 * 1024
MAX_WALK_FILES = 20000
SAFE_MODE_LINE = ("Drawbridge isn't answering, so I can't change things for you right now. Restarting it.")
_CODE_EXTS = frozenset({".js", ".mjs", ".cjs", ".mts", ".cts", ".ts", ".tsx", ".jsx", ".py", ".pyw", ".go", ".rs",
                        ".java", ".kt", ".kts", ".rb", ".php", ".cs", ".vue", ".svelte", ".html", ".htm", ".xhtml",
                        ".svg", ".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd", ".lua", ".pl", ".r",
                        ".swift", ".dart", ".scala", ".c", ".h", ".cpp", ".hpp", ".cc"})
#: Read whole and scanned. "" covers dotfiles and files with no extension.
_TEXT_EXTS = _CODE_EXTS | frozenset({".md", ".markdown", ".rst", ".txt", ".json", ".jsonc", ".json5", ".yml", ".yaml",
                                     ".toml", ".cfg", ".ini", ".conf", ".env", ".xml", ".css", ".scss", ".sass",
                                     ".less", ".csv", ".tsv", ".lock", ".sql", ".graphql", ".gql", ".map", ".tmpl",
                                     ".j2", ".hbs", ".ejs", ".properties", ".gradle", ".mod", ".sum", ".in", ""})
#: Plain text kinds apps ship that the list above does not name (D70): web app manifests, translation files,
#: TypeScript's build info and Mako templates. Read and scanned like other text, and stricter: see
#: :func:`_plain_text_kind`. Any other kind is read as text only when it is plain text (D78, :func:`_looks_text`).
_PLAIN_TEXT_EXTS = frozenset({".webmanifest", ".po", ".pot", ".tsbuildinfo", ".mako"})
#: A copy of a known text file kept as an example (``.env.example``, ``config.yml.dist``): plain text only
#: when the name without this last part is itself a known text kind.
_PLAIN_COPY_EXTS = frozenset({".example", ".sample", ".template", ".dist"})
#: ``Dockerfile.dev``, ``Containerfile.test``: the build files for one stage.
_PLAIN_BUILD_NAME = re.compile(r"^(?:dockerfile|containerfile)\.[a-z0-9_-]{1,24}$")
#: Known kinds that are never read as text, even when their bytes happen to be UTF-8 (D78): programs and
#: libraries, archives and installers, disk images, compiled or packed data, models and databases, key and
#: certificate files, and kinds Windows runs or opens by itself (shortcuts, registry files, HTML apps,
#: Windows script files). Any dotted part of a name counts (``setup.exe.sample``, ``libz.so.1``). A file of
#: one of these kinds is held as it always was; the pictures, fonts and sounds of ``_INERT_MAGIC`` count too.
_NEVER_TEXT_EXTS = frozenset({
    # programs, libraries, compiled code
    ".exe", ".dll", ".so", ".dylib", ".sys", ".drv", ".ocx", ".cpl", ".scr", ".com", ".pif", ".bin", ".elf",
    ".o", ".obj", ".a", ".lib", ".ko", ".pyc", ".pyo", ".pyd", ".class", ".dex", ".wasm", ".node", ".mo",
    ".beam", ".luac",
    # archives, packages, installers, disk images
    ".zip", ".jar", ".war", ".ear", ".aar", ".apk", ".aab", ".ipa", ".xpi", ".crx", ".vsix", ".nupkg", ".whl",
    ".egg", ".gem", ".tar", ".gz", ".tgz", ".bz2", ".tbz", ".tbz2", ".xz", ".txz", ".lz", ".lz4", ".lzma",
    ".zst", ".z", ".7z", ".rar", ".cab", ".cpio", ".ar", ".deb", ".rpm", ".msi", ".msix", ".msixbundle",
    ".appx", ".appxbundle", ".msp", ".mst", ".dmg", ".pkg", ".iso", ".img", ".vhd", ".vhdx", ".vmdk",
    ".qcow2", ".snap", ".appimage", ".svgz", ".br",
    # databases, models, packed data, office files with their own containers
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb", ".pkl", ".pickle", ".npy", ".npz", ".h5", ".hdf5",
    ".onnx", ".pt", ".pth", ".ckpt", ".safetensors", ".gguf", ".ggml", ".tflite", ".pb", ".parquet", ".avro",
    ".feather", ".arrow", ".doc", ".docx", ".docm", ".xls", ".xlsx", ".xlsm", ".xlam", ".xll", ".ppt",
    ".pptx", ".pptm", ".odt", ".ods", ".odp",
    # media not in _INERT_MAGIC
    ".tif", ".tiff", ".psd", ".heic", ".avif", ".mp4", ".m4a", ".m4v", ".mov", ".avi", ".mkv", ".aac",
    ".opus", ".wma", ".wmv", ".flv", ".eot", ".ttc",
    # keys and certificates (keys belong in the vault, never read as an app's text)
    ".pem", ".key", ".crt", ".cer", ".der", ".p7b", ".p7c", ".p8", ".p12", ".pfx", ".jks", ".keystore",
    ".kdbx", ".gpg", ".pgp", ".asc", ".ppk",
}) | kinds.WINDOWS_OPENS          # and kinds Windows runs or opens by itself, outside any app's run line
#: Three-letter kinds Windows runs or opens by itself. With 8.3 short names on, ``x.vbsx`` is also
#: ``X~1.VBS`` and ``evil.exeabc`` is also ``EVIL~1.EXE``, so a name whose last part is longer and starts
#: with one of these is held too (D78). Only the last part: a short name keeps only that one.
_SHORT_NAME_RUNS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".cpl", ".msi", ".msp", ".mst", ".msc", ".msu", ".lnk", ".url", ".scf",
    ".reg", ".inf", ".hta", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".wsc", ".sct", ".shb", ".shs", ".ins",
    ".isp", ".chm", ".hlp", ".rdp", ".jar", ".bat", ".cmd",
})
#: A README of one of these kinds (or with no extension) is a doc: words about a tunnel are not use of one.
#: A README of any other kind (``readme.sh``, ``README.command``) gets the tunnel check like code (D78).
_README_DOC_EXTS = frozenset({"", ".md", ".markdown", ".mdown", ".mkd", ".rst", ".txt", ".text", ".adoc",
                              ".asciidoc", ".org", ".textile", ".rdoc", ".pod"})
#: Characters that say a strict UTF-8 file is not plain text after all (D78): C0 controls other than tab,
#: line feed, form feed and carriage return; DEL; and the C1 controls. A NUL is one of them.
_NOT_TEXT_CHAR = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")
#: Pictures, fonts and sounds: not run as code. Each must start with its own kind's bytes, else can't check.
_INERT_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"), ".ico": (b"\x00\x00\x01\x00",), ".bmp": (b"BM",),
    ".webp": (b"RIFF",), ".woff": (b"wOFF",), ".woff2": (b"wOF2",), ".ttf": (b"\x00\x01\x00\x00", b"true"),
    ".otf": (b"OTTO",), ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), ".wav": (b"RIFF",),
    ".ogg": (b"OggS",), ".flac": (b"fLaC",), ".webm": (b"\x1a\x45\xdf\xa3",), ".pdf": (b"%PDF-",),
}

#: Real tunnel tools, USED in code (v1 classify._TUNNEL_RE, as-is). A link or a README explaining one is not use.
_TUNNEL_RE = re.compile(
    r"""(?:require\s*\(|import\s*\(|from\s+|import\s+)\s*['"](?:@ngrok/ngrok|ngrok|"""
    r"""pyngrok(?:\.ngrok)?|localtunnel)['"]"""
    r"|\bfrom\s+pyngrok\b|\bimport\s+pyngrok\b"
    r"|\bngrok\s+(?:http|tcp|tls|start)\b|\bngrok\.(?:connect|forward)\s*\("
    r"|\blocaltunnel\s*\(|\blt\s+--port\b"
    r"|\bcloudflared(?:\.exe)?\s+(?:tunnel|access\s+tcp)\b"
    r"|\bssh\b[^\n]{0,80}\s-R\s[^\n]{0,80}(?:serveo\.net|localhost\.run|bore\.pub)"
    r"|\bbore\s+local\b", re.I)
_URL_HOST_RE = re.compile(r"\bhttps?://([a-z0-9][a-z0-9.-]{0,252}[a-z0-9])(?::\d{1,5})?(?=[/\"'`\s)?#]|$)", re.I)
#: Hosts code names all the time that are not traffic (specs, schemas, examples).
_QUIET_HOSTS = ("example.com", "example.org", "example.net", "localhost", "w3.org", "schema.org", "json-schema.org",
                "xmlns.com", "purl.org", "ogp.me", "mozilla.org", "apache.org", "opensource.org", "spdx.org",
                "creativecommons.org", "semver.org", "github.com", "githubusercontent.com", "npmjs.com",
                "npmjs.org", "pypi.org", "python.org", "nodejs.org", "reactjs.org", "react.dev",
                "developer.mozilla.org", "wikipedia.org", "stackoverflow.com", "shields.io")

#: Words in a tool's name that say it changes something (X27: rules only add reviews).
_CHANGING_NAME = re.compile(
    r"(?:^|[_\-.])(?:add|create|new|insert|put|post|set|update|edit|modify|change|patch|write|save|store|"
    r"delete|del|remove|rm|clear|reset|wipe|purge|drop|destroy|send|email|mail|message|notify|publish|upload|"
    r"share|move|rename|copy|install|uninstall|run|exec|execute|start|stop|restart|kill|turn|toggle|switch|"
    r"enable|disable|lock|unlock|open|close|buy|pay|order|book|schedule|cancel|approve|reject|grant|revoke|"
    r"import|sync|play|pause|skip|call|trigger|apply|commit|push|deploy)(?:$|[_\-.])", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_reparse(st: os.stat_result) -> bool:
    attrs = getattr(st, "st_file_attributes", 0) or 0
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _entries(root: Path):
    """``(rel_posix, abs_path, size, kind)`` for EVERY entry under *root*, at any depth, nothing skipped.
    ``kind``: ``file`` (regular), ``link`` (a symlink or reparse point, never followed), ``other``.
    Folders that are links are reported, not entered."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        keep = []
        for d in sorted(dirnames):
            full = os.path.join(dirpath, d)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                st = os.lstat(full)
            except OSError:
                yield rel, full, 0, "other"
                continue
            if stat.S_ISLNK(st.st_mode) or _is_reparse(st):
                yield rel, full, 0, "link"
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                st = os.lstat(full)
            except OSError:
                yield rel, full, 0, "other"
                continue
            if stat.S_ISLNK(st.st_mode) or _is_reparse(st):
                yield rel, full, 0, "link"
            elif stat.S_ISREG(st.st_mode):
                yield rel, full, st.st_size, "file"
            else:
                yield rel, full, 0, "other"


def _read(path: str, limit: int = MAX_TEXT_READ) -> str | None:
    """Up to *limit* bytes as text, or None when the file can't be opened."""
    raw = _read_bytes(path, limit)
    return raw.decode("utf-8", "replace") if raw is not None else None


def _read_bytes(path: str, limit: int = MAX_TEXT_READ) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit)
    except OSError:
        return None


def _plain_text_kind(name: str) -> bool:
    """True for a file of one of the named plain text kinds that ``_TEXT_EXTS`` does not cover (D70).
    *name* is the lower-case file name. Such a file passes only as strict UTF-8 with no NUL byte, within
    the read limit (:func:`_strict_text`); it is scanned whatever it holds."""
    ext = os.path.splitext(name)[1]
    if ext in _PLAIN_TEXT_EXTS or _PLAIN_BUILD_NAME.match(name):
        return True
    if ext in _PLAIN_COPY_EXTS:
        base = name[:-len(ext)]
        if not base:
            return False
        inner = os.path.splitext(base)[1]
        return inner in _TEXT_EXTS or inner in _PLAIN_TEXT_EXTS or bool(_PLAIN_BUILD_NAME.match(base))
    return False


def _copied_name(name: str) -> str:
    """The name a ``.example``, ``.sample``, ``.template`` or ``.dist`` copy of a known kind stands for
    (``index.html.template`` -> ``index.html``), so it gets every check that kind gets; else *name*."""
    ext = os.path.splitext(name)[1]
    return name[:-len(ext)] if ext in _PLAIN_COPY_EXTS and _plain_text_kind(name) else name


def _strict_text(raw: bytes) -> bool:
    """Strict UTF-8 with no NUL byte."""
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return False
    return True


def _claims_never_text(name: str) -> bool:
    """True when any dotted part of the lower-case file *name* is a kind never read as text (D78):
    ``setup.exe.sample``, ``libz.so.1``, ``photo.png.example``, ``tool.exe.`` all are. So is a name whose
    8.3 short name would end in a kind Windows runs by itself (``x.vbsx``, ``evil.exeabc``)."""
    for part in name.split(".")[1:]:
        part = part.strip(" .")
        if part and ("." + part in _NEVER_TEXT_EXTS or "." + part in _INERT_MAGIC):
            return True
    return _short_name_runs(name)


def _short_name_runs(name: str) -> bool:
    """The 8.3 short name of the lower-case *name* would end in a kind Windows runs by itself (D78)."""
    stem = name.rstrip(" .")
    if "." in stem:
        last = stem.rsplit(".", 1)[1].replace(" ", "")
        if len(last) > 3 and "." + last[:3] in _SHORT_NAME_RUNS:
            return True
    return False


def _looks_text(raw: bytes) -> bool:
    """Plain text (D78): strict UTF-8 (a UTF-8 BOM is fine) with no NUL or other control character but
    tab, line feed, form feed and carriage return. UTF-16 and other encodings fail (their NUL bytes)."""
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        return False
    return not _NOT_TEXT_CHAR.search(text)


def _held_sort(full: str, name: str, size: int, head: bytes | None = None) -> tuple[str, str]:
    """Why a file it can't read is held, for the card (D95): a kind that can run code (named), a program by its
    first bytes, too big, or a kind it can't read."""
    if head is None:
        head = _read_bytes(full, 8) or b""
    got = kinds.category(name, head)
    if got.sort == "unknown" and size > MAX_TEXT_READ and not _claims_never_text(name):
        return ("too_big", "")
    return (got.sort, got.label)


def _starts_with(path: str, magics: tuple) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    return any(head.startswith(m) for m in magics)


def tree_sha(path: Path | str) -> str:
    """One hash over every entry's path, kind and content, at every depth. Nothing is skipped;
    a link is hashed by where it points, never followed."""
    h = hashlib.sha256()
    for rel, full, _size, kind in _entries(Path(path)):
        fh_hash = hashlib.sha256()
        if kind == "file":
            try:
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        fh_hash.update(chunk)
            except OSError:
                fh_hash.update(b"\x00unreadable")
        elif kind == "link":
            try:
                fh_hash.update(b"link\x00" + os.readlink(full).encode("utf-8", "replace"))
            except (OSError, ValueError):
                fh_hash.update(b"link\x00?")
        else:
            fh_hash.update(b"other")
        h.update(rel.encode("utf-8") + b"\x00" + kind.encode() + b"\x00" + fh_hash.digest())
    return h.hexdigest()


def held_fingerprints(root: Path | str, rels: Iterable[str]) -> dict[str, str]:
    """``{rel: sha256}`` for each held entry under *root*: a file by its bytes, a link by where it points
    (never followed), as :func:`tree_sha` sees an entry. A path that is missing, outside *root* or can't
    be read gets a value no entry ever has, so it never matches a Yes."""
    base = os.path.realpath(root)
    out: dict[str, str] = {}
    for rel in rels:
        rel = str(rel)
        full = os.path.join(base, *rel.split("/"))
        h = hashlib.sha256()
        try:
            if os.path.commonpath([base, os.path.realpath(os.path.dirname(full))]) != base:
                raise ValueError(rel)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode) or _is_reparse(st):
                h.update(b"link:" + os.readlink(full).encode("utf-8", "replace"))
            elif stat.S_ISREG(st.st_mode):
                h.update(b"file:")
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
            else:
                h.update(b"other:")
        except (OSError, ValueError):
            out[rel] = "unreadable-" + secrets.token_hex(8)
            continue
        out[rel] = h.hexdigest()
    return out


def mode_bound(verdict: dict, files_mode: str | None) -> bool:
    """D95: a verdict that passed files only under Normal (``eased``) still binds only while the owner's
    setting is Normal. *files_mode* None (the caller can't say) is never Normal."""
    try:
        eased = int(verdict.get("eased") or 0)
    except (TypeError, ValueError):
        return False
    return eased == 0 or (verdict.get("files_mode") == kinds.NORMAL and files_mode == kinds.NORMAL)


def verify_tree(path: Path | str, verdict: dict, files_mode: str | None = None) -> bool:
    """True only if *verdict* passed, the folder is byte-for-byte what was checked (X24), and a verdict that
    passed files only under Normal is checked while the setting is still Normal (D95)."""
    return (verdict.get("verdict") in (ALLOW, WARN) and mode_bound(verdict, files_mode)
            and verdict.get("tree_sha") == tree_sha(path))


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def owner_read_fields(manifest: dict) -> list[str]:
    """Every string of a manifest a person (or the brain) reads."""
    out: list[str] = []
    for key in ("name", "description", "consent"):
        out += list(_strings(manifest.get(key)))
    ui = manifest.get("ui") or {}
    for key in ("pages", "tray", "widgets"):
        for item in ui.get(key) or []:
            if isinstance(item, dict):
                out += [str(item.get(k)) for k in ("title", "label") if item.get(k)]
    for step in manifest.get("setup_steps") or []:
        out += list(_strings(step))
    needs = manifest.get("needs") or {}
    for k in needs.get("keys") or []:
        if isinstance(k, dict):
            out += [str(k.get(f)) for f in ("why", "get_it") if k.get(f)]
    ab = manifest.get("abilities") or {}
    out += list(_strings(ab.get("examples")))
    return [s for s in out if s]


#: The screen's reasons that mark a package for her review (as flagged owner-read fields do at install); a link
#: or markup in a tool's description only keeps it off her cards.
REVIEW_FLAGS = ("it reads like instructions to an AI", "it has hidden or control characters", "it is not plain text")


def screen_tool_words(tools: Iterable[dict]) -> dict[str, dict]:
    """A package's live MCP tool list (``list_tools``: ``name``, ``description``), screened like the words of its
    manifest (lane I8a): ``{tool name: {"why": plain reason, "review": bool}}`` for each flagged description.
    ``review`` is True for words aimed at an AI or at her answers and for hidden characters (the description is
    then dropped and the package waits for her review); a link or markup only keeps it off her cards."""
    out: dict[str, dict] = {}
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        why = text_rules.owner_text_flag(t.get("description") if t.get("description") is not None else "")
        if why:
            out[str(t.get("name") or "")] = {"why": why, "review": why in REVIEW_FLAGS}
    return out


def _row(check: str, result: str, code: str, why: str) -> dict:
    return {"check": check, "result": result, "code": code, "why": why}


REASON_WORDS = {
    "tunnel": ("its code can start a tunnel, and a tunnel can open your computer to the internet, so people out there "
               "could reach what runs here"),
    "hidden_loader": "its page hides encoded code that it unpacks and runs",
    "readme_hostile": "its README holds words aimed at turning an AI against you",
    "instruction_text": "some of its own words read like orders to an AI",
    "bad_host": "a place it wants to reach is not a plain internet name",
    "admin_not_verified": "only Verified providers may ask for admin rights",
    "too_big": "it is bigger than 4 GB",
    "check_failed": "the checks could not finish",
    "unknown_kind": "PuffPAI does not know what kind of thing it is",
    "not_a_folder": "there was no folder to check",
    "raw_reader": "it would see your real private values, so it stays off until you turn it on",
    "hosts_not_listed": "its code names places it did not list",
    "lines_hidden": "some lines in its README or docs read like words for an AI and are hidden from the model",
    "big": "it is over 1 GB",
    "cant_check": "some of its files could not be checked (too big, a link, or a kind PuffPAI can't read)",
}
#: Why a file is held, for the card (D95): :func:`kinds.held_words` turns these into plain lines.
_HELD_LINK, _HELD_OTHER, _HELD_CUT = ("link", ""), ("unreadable", ""), ("cut", "")
#: A file under ``docs/`` held for words aimed at an AI (D105). The same row and reason as any held file, so every
#: caller of a hold takes it as it takes the others; only the card's words differ.
_HELD_AI_WORDS = ("ai_words", "")
DOCS_WORDS = "some of its docs hold words aimed at an AI"
#: A file with a tunnel tool in its code (D117). The same row and reason again; it was read, so the card must not say
#: Drawbridge couldn't check it.
_HELD_TUNNEL = ("tunnel", "")
#: What a tunnel does, as its own line of the card, so a card whose lead says it can leave it off an app's line.
TUNNEL_DOES = "A tunnel can open your computer to the internet, so people out there could reach what runs here."
#: Beside the kinds of hit: a file held for those words that is itself code that can run (D105).
_HIT_CODE = "code"


def _hostile_words(rel: str) -> str:
    """The card's words for hostile text found in the file *rel*, naming the right place (D97): a README, or a
    file under ``docs/`` by its cleaned base name, never a folder path. A name that is not fit for a card (it
    reads like an order, or holds markup or a link) is left out: "its docs" is still true."""
    if rel.rsplit("/", 1)[-1].lower().startswith("readme"):
        return REASON_WORDS["readme_hostile"]
    shown = kinds.shown_name(rel)
    named = f" ({shown})" if shown and not text_rules.owner_text_flag(shown) else ""
    return f"its docs{named} hold words aimed at turning an AI against you"


def _tunnel_words(files: list[str]) -> list[str]:
    """The card's lines for files with a tunnel tool in their code (D117): a count only, then what a tunnel does. No
    file name, not even a plain one: ``no-tunnel-here.js`` must not speak on this line. The names stay in the row."""
    if not files:
        return []
    count = "1 file has" if len(files) == 1 else f"{len(files)} files have"
    return [f"{count} code that can start a tunnel.", TUNNEL_DOES]


def _docs_words(hits: dict[str, frozenset[str]]) -> list[str]:
    """The card's lines for files under ``docs/`` held for words aimed at an AI (D105): a count and at most three base
    names, never a path. A name that is not one plain token only counts (:func:`kinds.card_name`). Each kind of hit
    found anywhere in those files gets its sentence, and code that can run gets its own line.
    *hits*: ``{rel: kinds}``, from :func:`text_rules.hit_kinds` and :data:`_HIT_CODE`."""
    if not hits:
        return []
    names = kinds.card_names(hits)
    count = "1 file in its docs holds" if len(hits) == 1 else f"{len(hits)} files in its docs hold"
    line = f"{count} words aimed at an AI" + (f": {names}." if names else ".") + " Toolsmith does not see those lines."
    unseen = sum(1 for got in hits.values() if text_rules.HIT_UNSEEN in got)
    if unseen:
        line += (" 1 of them holds those words where a person may not see them." if unseen == 1
                 else f" {unseen} of them hold those words where a person may not see them.")
    keys = sum(1 for got in hits.values() if text_rules.HIT_KEYS in got)
    if keys:
        line += (" 1 of them asks an AI to send keys or passwords away." if keys == 1
                 else f" {keys} of them ask an AI to send keys or passwords away.")
    code = [rel for rel, got in hits.items() if _HIT_CODE in got]
    if not code:
        return [line]
    named = kinds.card_names(code)
    said = "1 of them is code that can run" if len(code) == 1 else f"{len(code)} of them are code that can run"
    return [line, said + (f": {named}." if named else ".")]


def _first_words(verdict: dict) -> str:
    """The plain words for the verdict's first reason: its own (``words``, D97), else the fixed ones. A hold with a
    tunnel leads with the tunnel, whatever else holds it (D117)."""
    if verdict.get("verdict") == HOLD and verdict.get("tunnels"):
        return REASON_WORDS["tunnel"]
    own = verdict.get("words") if isinstance(verdict.get("words"), dict) else {}
    return next((str(own.get(c) or REASON_WORDS[c]) for c in verdict.get("reasons") or [] if c in REASON_WORDS), "")


def line_for(verdict: dict) -> str:
    name = str(verdict.get("name") or "This package")
    v = verdict.get("verdict")
    if v == ALLOW:
        return f"{name}: passed every check."
    first = _first_words(verdict)
    if v == WARN:
        return f"{name}: safe to add, with something to know first: {first}." if first else \
            f"{name}: safe to add, with something to know first."
    if v == HOLD:
        return f"{name}: waiting for your Yes. {first[:1].upper() + first[1:]}." if first else             f"{name}: waiting for your Yes."
    return f"{name}: stopped because {first}. It was not added." if first else f"{name}: stopped. It was not added."


def _safe_name(manifest: dict) -> str:
    name = " ".join(str(manifest.get("name") or "").split())[:80]
    if name and not text_rules.instruction_shaped(name) and "://" not in name:
        return name
    return "This package"


def _hints(entry: Any) -> dict:
    ann = (entry or {}).get("annotations") if isinstance(entry, dict) else None
    return ann if isinstance(ann, dict) else {}


def tool_changes(name: str, manifest: dict | None, entry: dict | None = None, origin: str = "local",
                 read_only_marked: Iterable[str] = ()) -> bool:
    """THE one answer to "does this tool change something?" (X27). Every caller asks this function.

    Changing when ANY of these is true: the manifest lists it under ``abilities.changes``; its own MCP
    annotations say ``destructiveHint: true`` or ``readOnlyHint: false``; its name reads like a change
    (Drawbridge's rule); or Toolsmith wrote the manifest and the tool does not say ``readOnlyHint: true``.
    A name that is not a usable string counts as changing. The owner's read-only mark (D47) lowers
    only a tool the manifest does not list and that is not marked destructive. Annotations are the
    package's own claims, so they only ever add a review."""
    if not isinstance(name, str) or not name:
        return True
    manifest = manifest if isinstance(manifest, dict) else {}
    declared = set((manifest.get("abilities") or {}).get("changes") or [])
    ann = _hints(entry)
    changing = (name in declared or ann.get("destructiveHint") is True or ann.get("readOnlyHint") is False
                or bool(_CHANGING_NAME.search(name)))
    if not changing and str(origin).startswith(("adopted:", "made")) and ann.get("readOnlyHint") is not True:
        changing = True                       # unclassified adopted tool: changing until she says otherwise
    if changing and name in set(read_only_marked) and name not in declared and ann.get("destructiveHint") is not True:
        changing = False                      # her own mark; never over the manifest or a destructive hint
    return changing


def classify_tools(manifest: dict, tools: Iterable[dict] | None, origin: str = "local",
                   read_only_marked: Iterable[str] = ()) -> dict[str, str]:
    """``{tool: "changing" | "read_only"}`` (X27), each by :func:`tool_changes`. *tools* are MCP
    ``list_tools`` entries (``{name, annotations: {readOnlyHint, destructiveHint}}``); *read_only_marked*
    are tools the owner marked read-only herself (only she can remove a review)."""
    declared = set((manifest.get("abilities") or {}).get("changes") or [])
    names = set(declared)
    entries = {}
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("name"), str):
            names.add(t["name"])
            entries[t["name"]] = t
    return {name: "changing" if tool_changes(name, manifest, entries.get(name), origin, read_only_marked)
            else "read_only" for name in sorted(names)}


@dataclass
class Scope:
    """What the owner approved for one package (from its install Yes; 09 section 6 item 6)."""

    tools: dict[str, str] = field(default_factory=dict)          # tool -> changing | read_only
    approved: set[str] = field(default_factory=set)              # changing tools that "don't ask"
    ask: set[str] = field(default_factory=set)                   # tools she set to ask, or needs_yes
    targets: dict[str, dict[str, set]] = field(default_factory=dict)   # tool -> arg -> allowed values
    floor_tools: set[str] = field(default_factory=set)           # network-device changes (D22, D29)
    has_hosts: bool = False                                       # it may reach the internet


class Drawbridge:
    """Drawbridge's checks. One per Core. ``audit`` is the Core's chained audit log."""

    name = "drawbridge"

    def __init__(self, audit: ChainLog | None = None, *, files_mode: Any = None) -> None:
        self.audit = audit
        self._scopes: dict[str, Scope] = {}
        self._lock = threading.Lock()
        self.ready = True
        # D95: the owner's setting for files it can't read, a value or a function that reads it now. A bare
        # Drawbridge (no setting given) is Locked down: it never passes more than before D95.
        self._files_mode = files_mode

    def mode(self) -> str:
        """The owner's setting now: "normal" or "locked". Anything it can't read is "locked"."""
        try:
            got = self._files_mode() if callable(self._files_mode) else self._files_mode
        except Exception:  # noqa: BLE001 - a setting that can't be read never loosens a check
            return kinds.LOCKED
        return got if got in kinds.MODES else kinds.LOCKED

    def verify(self, path: Path | str, verdict: dict) -> bool:
        """:func:`verify_tree` under the owner's setting now (D95)."""
        return verify_tree(path, verdict, self.mode())

    # -- install check ------------------------------------------------------
    def check_package(self, path: Path | str | None, manifest: dict, *, origin: str = "local",
                      tools: Iterable[dict] | None = None) -> dict:
        """Check one staged folder before install. Never raises: a crash is a block."""
        checked_at = _now()
        mode = self.mode()
        try:
            verdict = self._check(Path(path) if path else None, manifest if isinstance(manifest, dict) else {},
                                  origin, tools, mode)
        except Exception:  # noqa: BLE001 - a check that crashed is a stop, never a pass
            verdict = {"verdict": BLOCK, "reasons": ["check_failed"], "rows": [], "tree_sha": "",
                       "tools": {}, "hidden_lines": 0}
        verdict["files_mode"] = mode
        verdict.setdefault("eased", 0)
        verdict["checked_at"] = checked_at
        verdict["name"] = _safe_name(manifest if isinstance(manifest, dict) else {})
        verdict["id"] = str((manifest or {}).get("id") or "")[:40] if isinstance(manifest, dict) else ""
        verdict["line"] = line_for(verdict)
        self._audit({"actor": "drawbridge", "action": "check_package", "package": verdict["id"] or "unknown",
                     "verdict": verdict["verdict"], "codes": verdict["reasons"][:10],
                     "tree_sha": verdict["tree_sha"] or None, "files_mode": mode,
                     "docs_words": verdict.get("docs_words") or 0, "tunnels": verdict.get("tunnels") or 0})
        return verdict

    def _check(self, root: Path | None, manifest: dict, origin: str, tools, mode: str = kinds.LOCKED) -> dict:
        rows: list[dict] = []
        blocks: list[str] = []
        holds: list[str] = []
        warns: list[str] = []
        kind = manifest.get("kind")
        if kind not in KINDS:
            blocks.append("unknown_kind")
        if root is None or not root.is_dir():
            blocks.append("not_a_folder")
            return {"verdict": BLOCK, "reasons": blocks, "rows": rows, "tree_sha": "", "tools": {}, "hidden_lines": 0}
        # 1. the manifest's own words and hosts
        if any(text_rules.instruction_shaped(s) for s in owner_read_fields(manifest)):
            blocks.append("instruction_text")
            rows.append(_row("manifest_words", "fail", "instruction_text", REASON_WORDS["instruction_text"]))
        needs = manifest.get("needs") or {}
        host_problems = check_hosts(needs.get("hosts") or []) + check_hosts(needs.get("install_hosts") or [])
        if host_problems:
            blocks.append("bad_host")
            rows.append(_row("hosts", "fail", "bad_host", REASON_WORDS["bad_host"]))
        community = not str(origin).startswith(("core", "store:verified"))
        if kind == "provider" and needs.get("admin") is True and community:
            blocks.append("admin_not_verified")
        if kind == "provider" and manifest.get("reads") == "raw" and community:
            holds.append("raw_reader")
        # 2. the files
        total = 0
        tunnels: list[str] = []                     # every file with a tunnel tool in its code (D117)
        hidden = ""
        readme_hit = ""
        docs_hits: dict[str, frozenset[str]] = {}   # files under docs/ held for words aimed at an AI (D105)
        hidden_lines = 0
        named_hosts: set[str] = set()
        cant: list[str] = []
        sorts: dict[str, tuple[str, str]] = {}      # why each file is held, for the card's words (D95)
        eased = 0                                   # files that passed only because the setting is Normal
        unpack = kinds.budget()                     # what this whole check may unpack from archives (D95)

        def hold(rel: str, why: tuple[str, str]) -> None:
            if rel not in sorts:
                cant.append(rel)
                sorts[rel] = why

        def tunnel_in_held(rel: str, full: str) -> None:
            """A file held unread can still hide a tunnel (D117). Read for that only: its first bytes, with "replace". It
            is a hold already, so this only changes what her card says."""
            raw = _read_bytes(full, MAX_TEXT_READ)
            if raw is not None and _TUNNEL_RE.search(raw.decode("utf-8", "replace")):
                tunnels.append(rel)

        count = 0
        for rel, full, size, kind in _entries(root):
            count += 1
            total += size
            if count > MAX_WALK_FILES:
                hold(rel, _HELD_CUT)
                break
            if kind != "file":
                hold(rel, _HELD_LINK if kind == "link" else _HELD_OTHER)   # never followed
                continue
            name = rel.rsplit("/", 1)[-1].lower()
            ext = os.path.splitext(name)[1]
            if ext in _INERT_MAGIC or (kinds.passable(name) and not _short_name_runs(name)):
                # D95: a kind that can't run code passes only when it really is that kind. The kinds with
                # first bytes listed in _INERT_MAGIC passed before D95 and pass in both modes; the rest pass
                # only under Normal. Locked down holds them, saying they would pass under Normal.
                got = (kinds.check_file(full, name, size, unpack) if ext not in _INERT_MAGIC
                       or _starts_with(full, _INERT_MAGIC[ext]) else kinds.Verdict(False, "not_kind"))
                if got.ok and ext in _INERT_MAGIC:
                    continue
                if got.ok and mode == kinds.NORMAL:
                    eased += 1
                    continue
                hold(rel, ("locked", kinds.group_word(name)) if got.ok else (got.sort, got.label))
                # Held. Still, a file that is plain text after all (a "picture" that is a script) is read and
                # gets every check code gets, as any plain text file did before D95: never looser than before.
                raw = _read_bytes(full, MAX_TEXT_READ + 1) if size <= MAX_TEXT_READ else None
                if raw is None or len(raw) > MAX_TEXT_READ or not _looks_text(raw):
                    tunnel_in_held(rel, full)
                    continue
                text = raw.decode("utf-8", "strict")
                plain, unknown = False, True
            else:
                plain = ext not in _TEXT_EXTS and _plain_text_kind(name)
                unknown = ext not in _TEXT_EXTS and not plain
                text = None
            if text is not None:
                pass
            elif unknown:
                # D78: a kind no list names is read as text when it is plain text, and then gets every
                # check code gets. A kind that is never text, a file over the read limit, or one that is not
                # strict UTF-8 plain text is held, read only for a tunnel (D117).
                if _claims_never_text(name) or size > MAX_TEXT_READ:
                    hold(rel, _held_sort(full, name, size))
                    tunnel_in_held(rel, full)
                    continue
                raw = _read_bytes(full, MAX_TEXT_READ + 1)
                if raw is None or len(raw) > MAX_TEXT_READ or not _looks_text(raw):
                    hold(rel, _HELD_OTHER if raw is None else _held_sort(full, name, size, raw[:8]))
                    tunnel_in_held(rel, full)
                    continue
                text = raw.decode("utf-8", "strict")
            else:
                if size > MAX_TEXT_READ:
                    hold(rel, ("too_big", ""))   # read and scanned up to the limit; the rest is unchecked
                if plain or not ext:
                    raw = _read_bytes(full, MAX_TEXT_READ)
                    if raw is None:
                        hold(rel, _HELD_OTHER)
                        continue
                    # Not plain text after all (D70; D78 for a file with no extension, such as a program
                    # with no ``.exe``): held, and still scanned below.
                    if size <= MAX_TEXT_READ and not (_strict_text(raw) if plain else _looks_text(raw)):
                        hold(rel, ("not_kind", "") if plain else _held_sort(full, name, size, raw[:8]))
                    text = raw.decode("utf-8", "replace")
                else:
                    text = _read(full, MAX_TEXT_READ)
            if text is None:
                hold(rel, _HELD_OTHER)
                continue
            # A copy (index.html.template) is checked as the kind it copies (index.html): every check (D70).
            kind_name = _copied_name(name) if plain else name
            kind_ext = os.path.splitext(kind_name)[1] if plain else ext
            is_doc = kind_ext in (".md", ".txt", ".rst") or (name.startswith("readme")
                                                              and kind_ext in _README_DOC_EXTS)
            if not is_doc and _TUNNEL_RE.search(text):
                tunnels.append(rel)                     # D117: listed even when it is held for another reason too
                hold(rel, _HELD_TUNNEL)
            # An unknown kind, and a file with no extension (a script can run with none), gets the
            # hidden-loader check whatever its name (D78).
            if not hidden and (hidden_loader.found(text) if unknown or not ext
                               else hidden_loader.in_file(kind_name if plain else rel, text)):
                hidden = rel
            if name.startswith("readme") or rel.startswith("docs/"):
                why = text_rules.readme_block(text)
                hidden_text, n = text_rules.hide_ai_lines(text)
                hidden_lines += n
                if why:
                    # D105: under Normal a hit in a file under docs/ is a hold, not a stop, but only when hiding
                    # its lines really takes it out. A README stops wherever it sits, because Toolsmith reads
                    # READMEs. Locked down stops, as before D105.
                    if (mode == kinds.NORMAL and rel.startswith("docs/") and not name.startswith("readme")
                            and not text_rules.readme_block(hidden_text.replace(text_rules.HIDDEN_LINE, ""))):
                        runs = kind_ext in _CODE_EXTS or ((unknown or not ext) and text.lstrip("\ufeff").startswith("#!"))
                        docs_hits[rel] = text_rules.hit_kinds(text) | ({_HIT_CODE} if runs else frozenset())
                        hold(rel, _HELD_AI_WORDS)
                    elif not readme_hit:
                        readme_hit = rel
            if (unknown or kind_ext in _CODE_EXTS) and len(named_hosts) < 200:
                for m in _URL_HOST_RE.finditer(text):
                    named_hosts.add(m.group(1).lower().rstrip("."))
        if hidden:
            blocks.append("hidden_loader")
            rows.append(_row("hidden_loader", "fail", "hidden_loader", f"{REASON_WORDS['hidden_loader']} ({hidden})"))
        words: dict[str, str] = {}
        if readme_hit:
            blocks.append("readme_hostile")
            words["readme_hostile"] = _hostile_words(readme_hit)
            rows.append(_row("readme", "fail", "readme_hostile", words["readme_hostile"]))
        if hidden_lines:
            warns.append("lines_hidden")
            rows.append(_row("readme", "note", "lines_hidden", f"{hidden_lines} line(s) hidden from the model"))
        if cant:
            holds.append("cant_check")
            # A tunnel leads (D117): first in the card's lines, and the row's and the verdict's own words.
            said = _tunnel_words(tunnels) + kinds.held_words(sorts) + _docs_words(docs_hits)
            # Only words aimed at an AI (D105): every file was read, so the card must not say otherwise.
            docs_only = bool(docs_hits) and not tunnels and all(why == _HELD_AI_WORDS for why in sorts.values())
            lead = REASON_WORDS["tunnel"] if tunnels else DOCS_WORDS if docs_only else REASON_WORDS["cant_check"]
            if tunnels:
                words["cant_check"] = REASON_WORDS["tunnel"]
            elif docs_only:
                words["cant_check"] = f"{DOCS_WORDS}, and Toolsmith does not see those lines"
            held = _row("files", "note", "cant_check", f"{lead}: " + " ".join(said))
            held["docs_only"] = docs_only
            held["docs_words"] = list(docs_hits)
            held["tunnel"] = list(tunnels)
            # The card's plain words (D95): counts by kind, at most three base names a line, never a path.
            held["said"] = said
            held["files"] = cant[:20]
            held["more"] = max(0, len(cant) - 20)
            # Every held path (not shown; the Core's own use): a hold she said Yes to during a try is matched
            # file by file at install (``held_fingerprints``). ``cut``: the walk stopped, so the rest is unseen.
            held["all"] = list(cant)
            held["cut"] = count > MAX_WALK_FILES
            rows.append(held)
        if total > DISK_BOUND:
            blocks.append("too_big")
        elif total > DISK_NOTE:
            warns.append("big")
        listed = {h.split(":")[0].lower() for h in (needs.get("hosts") or []) + (needs.get("install_hosts") or [])}
        unlisted = sorted(h for h in named_hosts
                          if h not in listed and not any(h == q or h.endswith("." + q) for q in _QUIET_HOSTS)
                          and not any(h.endswith("." + x) for x in listed) and "." in h)
        if unlisted:
            warns.append("hosts_not_listed")
            rows.append(_row("hosts_vs_code", "note", "hosts_not_listed", ", ".join(unlisted[:20])))
        rows.append(_row("files", "pass", "", f"{total} bytes checked"))
        if eased:
            rows.append(_row("kinds", "pass", "", f"{eased} file(s) passed as pictures, sound, video, fonts, "
                                                  "models, documents or archives of those (your setting: Normal)"))
        verdict = BLOCK if blocks else HOLD if holds else WARN if warns else ALLOW
        return {"verdict": verdict, "reasons": blocks or holds or warns, "rows": rows,
                "tree_sha": tree_sha(root), "tools": classify_tools(manifest, tools, origin),
                "hidden_lines": hidden_lines, "eased": eased + len(docs_hits), "docs_words": len(docs_hits),
                "tunnels": len(tunnels), "words": words}

    # -- everyday review ----------------------------------------------------
    def reaches_out(self, package_id: str) -> None:
        """She allowed this package an address (D110): from now on a private detail handed to it asks first."""
        with self._lock:
            scope = self._scopes.get(package_id)
            if scope is not None:
                scope.has_hosts = True

    def set_scope(self, package_id: str, scope: Scope) -> None:
        """The Core sets this from her install Yes and her later choices (never a package or Toolsmith)."""
        with self._lock:
            self._scopes[package_id] = scope

    def drop_scope(self, package_id: str) -> None:
        with self._lock:
            self._scopes.pop(package_id, None)

    def see_tools(self, package_id: str, manifest: dict | None, tools: Iterable[dict], origin: str = "local") -> None:
        """The package's live tool list (``list_tools``, with its annotations), classified by
        :func:`tool_changes`. It only ever ADDS reviews: a new tool is classified, a read-only tool
        that now reads as changing becomes changing; nothing changing is lowered here, and nothing is
        added to her approved scope (only her install Yes does that)."""
        live = classify_tools(manifest if isinstance(manifest, dict) else {}, tools, origin)
        with self._lock:
            scope = self._scopes.get(package_id)
            if scope is None:
                return
            for name, kind in live.items():
                if scope.tools.get(name) != "changing":
                    scope.tools[name] = kind

    def is_changing(self, package_id: str, tool: str) -> bool:
        """Drawbridge's classification of one installed tool; unknown counts as changing (X27)."""
        with self._lock:
            scope = self._scopes.get(package_id)
            kind = scope.tools.get(tool) if scope is not None else None
        return kind != "read_only"

    def review_action(self, package: str, tool: str, args: dict | None = None, *,
                      labels: Iterable[str] = (), caller: str = "concierge") -> dict:
        """One tool call (every call, read-only ones too).
        ``{verdict: allow|ask|deny, reason_code, review_id, floor, line, changing}``; ``changing`` is
        Drawbridge's own classification (unknown counts as changing)."""
        try:
            if not self.ready:
                raise RuntimeError("down")
            out = self._review(package, tool, args or {}, list(labels))
        except Exception:  # noqa: BLE001 - a missing or broken check refuses at once (X26)
            out = {"verdict": "deny", "reason_code": "drawbridge_down", "floor": False, "line": SAFE_MODE_LINE}
        try:
            out["changing"] = out.get("reason_code") != "read_only" and (
                out.get("reason_code") == "drawbridge_down" or self.is_changing(package, tool))
        except Exception:  # noqa: BLE001 - unsure means changing
            out["changing"] = True
        out["review_id"] = "rv_" + secrets.token_hex(8)
        if out["verdict"] != "allow" or out["reason_code"] == "in_scope":
            self._audit({"actor": "drawbridge", "action": "review", "package": str(package)[:40],
                         "tool": str(tool)[:64], "caller": str(caller)[:40], "verdict": out["verdict"],
                         "code": out["reason_code"], "review_id": out["review_id"]})
        return out

    def _review(self, package: str, tool: str, args: dict, labels: list[str]) -> dict:
        with self._lock:
            scope = self._scopes.get(package)
        if scope is None:
            return {"verdict": "deny", "reason_code": "not_installed", "floor": False,
                    "line": "That app is not installed, so nothing was changed."}
        kind = scope.tools.get(tool)
        if kind is None:
            return {"verdict": "ask", "reason_code": "unclassified", "floor": False,
                    "line": "This tool asks each time until you mark it read-only."}
        if any(is_secret_label(x) for x in labels):
            return {"verdict": "deny", "reason_code": "labelled", "floor": True,
                    "line": "A key or password never leaves your computer."}
        if labels and scope.has_hosts:
            return {"verdict": "ask", "reason_code": "labelled", "floor": True,
                    "line": "This would hand a private detail to an app that uses the internet."}
        if kind == "read_only":
            return {"verdict": "allow", "reason_code": "read_only", "floor": False, "line": ""}
        if tool in scope.floor_tools:
            return {"verdict": "ask", "reason_code": "floor_level", "floor": True,
                    "line": "This changes your network, so it needs your PIN" + (" or Windows Hello." if os.name == "nt" else ".")}
        if tool in scope.ask or tool not in scope.approved:
            return {"verdict": "ask", "reason_code": "needs_yes", "floor": False, "line": ""}
        for arg, allowed in (scope.targets.get(tool) or {}).items():
            if arg in args and args[arg] not in allowed:
                return {"verdict": "ask", "reason_code": "outside_scope", "floor": False, "line": ""}
        return {"verdict": "allow", "reason_code": "in_scope", "floor": False, "line": ""}

    # -- the Bus reviewer (envelopes with changes: true) -----------------------
    def review(self, envelope: Any, target: str = "") -> Any:
        from ..bus import ReviewVerdict
        payload = getattr(envelope, "payload", {}) or {}
        v = self.review_action(str(payload.get("package") or target), str(payload.get("tool") or envelope.intent),
                               payload.get("args") if isinstance(payload.get("args"), dict) else {},
                               labels=payload.get("labels") or (), caller=getattr(envelope, "origin_agent", ""))
        return ReviewVerdict(allowed=v["verdict"] == "allow", verdict="allow" if v["verdict"] == "allow" else "deny",
                             reason=v["reason_code"])

    def _audit(self, line: dict) -> None:
        if self.audit is None:
            return
        try:
            self.audit.append(line)
        except Exception:  # noqa: BLE001 - the verdict stands; a lost line is evidence lost, not a pass
            pass


__all__ = ["ALLOW", "BLOCK", "Drawbridge", "HOLD", "SAFE_MODE_LINE", "Scope", "WARN", "classify_tools",
           "held_fingerprints", "line_for", "mode_bound", "owner_read_fields", "screen_tool_words", "tool_changes",
           "tree_sha", "verify_tree"]
