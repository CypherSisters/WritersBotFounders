# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/box/cli_tools.py (as-is): parse_help, list_subcommands, derive_specs
"""Read a program's own ``--help`` text: what it takes, and its subcommands (D16, D138, ``zone2.programs``)."""

from __future__ import annotations

import os
import re
from typing import Callable, Optional

STDOUT_MAX = 65536
HELP_MAX_BYTES = 65536
WALL_S = 60
MAX_HELP_CALLS = 24
MAX_ACTIONS = 20
MAX_EXTS = 8

#: Subcommands that are never actions (servers, shells, self-changing ones).
SKIP_SUBCOMMANDS = frozenset({
    "help", "server", "serve", "daemon", "watch", "completion", "completions", "shell",
    "repl", "console", "init", "install", "uninstall", "update", "upgrade", "self-update",
    "selfupdate", "login", "logout", "auth", "config", "publish", "deploy", "version",
    "lsp"})
_BIN_PATH_RE = re.compile(r"^/work/(node_modules/\.bin|venv/bin)/([A-Za-z0-9][A-Za-z0-9._-]{0,63})\Z")
_WORD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}\Z")
_BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_FILE_WORDS = ("FILE", "PATH", "INPUT", "INFILE")

_HEADING_RE = re.compile(r"^\s*(?:available\s+)?(?:sub)?commands\s*:?\s*\Z", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^\S.*:\s*\Z")
_ITEM_RE = re.compile(r"^\s{1,10}([a-z][a-z0-9_-]{0,30})(?:,\s*[a-z][a-z0-9_-]{0,30})*"
                      r"(?:\s{2,}\S.*)?\s*\Z")
_OUTPUT_RE = re.compile(r"(?:^|[\s,\[])(?:-o|--output|--out|--outfile|--output-file|"
                        r"--output-dir|--outdir)(?=[\s,=\]<]|\Z)", re.M)
_EXT_RE = re.compile(r"(?:(?<=\s)|(?<=\*)|(?<=\()|(?<=\A))\.([a-z0-9]{1,6})(?=[\s),;]|\Z)",
                     re.IGNORECASE)


def _usage_block(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*usage\s*:\s*(.*)\Z", line, re.IGNORECASE)
        if not m:
            continue
        out = [m.group(1)]
        for more in lines[i + 1:i + 12]:
            if not more.strip() or not more[:1].isspace():
                break
            out.append(more.strip())
        return " ".join(out)
    return ""


def _strip_groups(usage: str) -> tuple[str, list[str]]:
    """Take option groups out of a usage line; returns (rest, {a,b,c} choices).

    Brackets are read by depth: a top-level ``[...]`` group whose content
    starts with ``-`` is an option group and goes whole, nested parts and
    all (``[--null-value X [X ...]]``); other groups stay (``[FILE]``)."""
    choices: list[str] = []
    for m in re.finditer(r"\{([a-z0-9_,-]{1,300})\}", usage):
        choices += [c for c in m.group(1).split(",") if _WORD_RE.match(c)]
    out: list[str] = []
    i, n = 0, len(usage)
    while i < n:
        ch = usage[i]
        if ch == "[":
            depth, j = 0, i
            while j < n:
                if usage[j] == "[":
                    depth += 1
                elif usage[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            group = usage[i:j + 1]
            if group[1:].lstrip().startswith("-"):
                out.append(" ")
            else:
                out.append(group)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    text = "".join(out)
    text = re.sub(r"(?:^|\s)--?[A-Za-z][\w-]*(?:[ =](?:[A-Z_]{2,}|<[^>]+>))?", " ", text)
    return text, choices


_ARGS_HEADING_RE = re.compile(r"^\s*(?:positional\s+)?arguments\s*:?\s*\Z", re.IGNORECASE)
_ARG_ENTRY_RE = re.compile(r"^(\s{1,10})[\[<]?([A-Za-z][A-Za-z0-9_-]{0,30})[\]>]?(?:\.\.\.)?"
                           r"(?:\s{2,}(\S.*))?\s*\Z")
_FILE_DESC_RE = re.compile(r"\b(?:files?|paths?)\b", re.IGNORECASE)
_DIR_DESC_RE = re.compile(r"\bdirector(?:y|ies)\b|\bfolders?\b", re.IGNORECASE)


def _argument_descriptions(text: str) -> dict:
    """``{NAME: {"first": first description line, "all": whole block}}`` from
    an "Arguments:" section."""
    out: dict = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _ARGS_HEADING_RE.match(lines[i]):
            i += 1
            continue
        i += 1
        current = None
        indent = None
        while i < len(lines):
            line = lines[i]
            if line.strip() and not line[:1].isspace():
                break                                   # the next section
            m = _ARG_ENTRY_RE.match(line)
            if m and (indent is None or len(m.group(1)) <= indent):
                indent = len(m.group(1))
                current = m.group(2).upper()
                first = (m.group(3) or "").strip()
                out.setdefault(current, {"first": first, "all": first})
            elif current and line.strip():
                entry = out[current]
                if not entry["first"]:
                    entry["first"] = line.strip()
                entry["all"] = (entry["all"] + " " + line.strip())[:2000]
            i += 1
    return {k: {"first": v["first"][:200], "all": v["all"]} for k, v in out.items()}


#: Lower-case usage words that name the file a command reads, as some tools
#: write them ("markdown file-to-convert [options]", "tool filename").
_LOWER_FILE_PARTS = frozenset({"file", "filename", "filepath", "infile", "inputfile",
                               "input"})


def _names_a_file(tok: str) -> bool:
    """True for a lower-case usage word made of plain parts, one of which
    names a file (``file-to-convert``, ``input_file``, ``filename``)."""
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+){0,4}", tok):
        return False
    return any(part in _LOWER_FILE_PARTS for part in re.split(r"[-_]", tok))


def _positionals(usage: str, descriptions: Optional[dict] = None) -> tuple[list[dict], bool, list[str]]:
    """``([{"name","file","required"}], takes_a_subcommand, {choices})``.

    A positional is a file when its name says so (FILE, PATH, INPUT) or its
    own description in the help's "Arguments" section says it is a file or a
    path (and not a folder)."""
    descriptions = descriptions or {}
    if not usage:
        return [], False, []
    rest, choices = _strip_groups(usage)
    tokens = rest.split()[1:]                # drop the program's own name(s) below
    out: list[dict] = []
    sub = bool(choices)
    for raw in tokens:
        tok = raw.strip(".,")
        required = True
        if tok.startswith("[") and tok.endswith("]"):
            required, tok = False, tok[1:-1]
        elif tok.startswith("["):
            required, tok = False, tok[1:]
        tok = tok.rstrip("]").strip(".")
        if tok.startswith("<") and tok.endswith(">"):
            tok = tok[1:-1]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,30}", tok):
            continue
        upper = tok.upper()
        if upper in ("OPTIONS", "OPTION", "ARGS", "ARGUMENTS", "FLAGS"):
            continue
        if upper in ("COMMAND", "SUBCOMMAND", "CMD"):
            sub = True
            continue
        if tok != upper and not raw.startswith(("<", "[<")) and not _names_a_file(tok):
            continue                          # a lower-case word: part of the name
        name = re.sub(r"[^a-z0-9]+", "_", tok.lower()).strip("_")[:39] or "arg"
        desc = str((descriptions.get(upper) or {}).get("first") or "")
        is_file = any(w in upper for w in _FILE_WORDS) or (
            bool(_FILE_DESC_RE.search(desc)) and not _DIR_DESC_RE.search(desc))
        if is_file:
            name = "file"
        if any(p["name"] == name for p in out):
            continue
        out.append({"name": name, "file": is_file, "required": required,
                    "about": str((descriptions.get(upper) or {}).get("all") or "")})
    return out, sub, choices


def parse_help(text: str) -> dict:
    """What a command's own ``--help`` says about using it. Plain data.

    ``{"subcommands": [names], "positionals": [{"name","file","required"}],
    "has_output_option": bool, "exts": [".x"], "takes_subcommand": bool}``.
    The text is untrusted: only short, shape-checked names come out of it.
    """
    text = str(text or "")[:HELP_MAX_BYTES]
    subs: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _HEADING_RE.match(lines[i]):
            i += 1
            while i < len(lines):
                line = lines[i]
                if not line.strip():
                    if subs:
                        break
                    i += 1
                    continue
                if _ANY_HEADING_RE.match(line) and not line[:1].isspace():
                    break
                m = _ITEM_RE.match(line)
                if m and m.group(1) not in subs:
                    subs.append(m.group(1))
                i += 1
            continue
        i += 1
    positionals, takes_sub, choices = _positionals(_usage_block(text),
                                                   _argument_descriptions(text))
    for c in choices:
        if c not in subs:
            subs.append(c)
    exts: list[str] = []
    # Extensions from the file argument's own description when it has one,
    # else from the whole help (never output formats of other options).
    about = " ".join(p.get("about") or "" for p in positionals if p["file"])
    for m in _EXT_RE.finditer(" " + about if about.strip() else text):
        ext = "." + m.group(1).lower()
        if ext not in exts and not ext[1:].isdigit():
            exts.append(ext)
        if len(exts) >= MAX_EXTS:
            break
    for p in positionals:
        p.pop("about", None)
    return {"subcommands": subs[:40], "positionals": positionals,
            "has_output_option": bool(_OUTPUT_RE.search(text)), "exts": exts,
            "takes_subcommand": takes_sub or bool(subs)}


#: Headings over a list of subcommands: "Commands:", "Available Commands:",
#: "Common Commands:", "Management Commands:", "All commands:", "Subcommands:".
_LIST_HEADING_RE = re.compile(r"^\s*(?:[A-Za-z]+\s+){0,2}(?:sub)?commands\s*:\s*\Z",
                              re.IGNORECASE)
#: git's own heading: "These are common Git commands used in various situations:"
#: (groups of indented "   clone   Clone a repository ..." lines under plain titles).
_GIT_HEADING_RE = re.compile(r"^these are (?:the )?common \S+ commands\b.*:\s*\Z", re.IGNORECASE)
_LIST_ITEM_RE = re.compile(r"^\s{1,10}([a-z][a-z0-9_-]{0,30})\*?"
                           r"(?:,\s*[a-z][a-z0-9_-]{0,30})*(?:\s{2,}(\S.*))?\s*\Z")
_COMMA_LIST_RE = re.compile(r"^\s{1,10}[a-z][a-z0-9_-]{0,30}(?:,\s*[a-z][a-z0-9_-]{0,30})+,?\s*\Z")
LIST_MAX = 60


def _plain_about(text: str) -> str:
    """A help line's description as short plain text (it is the program's
    own words: shown, never run)."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(text or ""))
    return " ".join(text.split())[:120]


def list_subcommands(text: str, limit: int = LIST_MAX) -> list[tuple[str, str]]:
    """``[(name, description)]`` of the subcommands a program's own help
    lists: "Commands:" style sections (docker, cobra and click tools), npm's
    comma list under "All commands:", and git's "These are common Git
    commands..." groups. Shape-checked names only; at most *limit*."""
    out: list[tuple[str, str]] = []
    seen: set = set()

    def add(name: str, about: str = "") -> None:
        if name not in seen and _WORD_RE.match(name) and len(out) < limit:
            seen.add(name)
            out.append((name, _plain_about(about)))

    lines = str(text or "")[:HELP_MAX_BYTES].splitlines()
    i = 0
    while i < len(lines):
        git_style = bool(_GIT_HEADING_RE.match(lines[i].strip()))
        if not (git_style or _LIST_HEADING_RE.match(lines[i])):
            i += 1
            continue
        i += 1
        found = False
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                if found and not git_style:
                    break
                i += 1
                continue
            if not line[:1].isspace():
                if git_style and not _LIST_HEADING_RE.match(line):
                    i += 1                       # a group title ("start a working area")
                    continue
                break                            # the next section
            if _COMMA_LIST_RE.match(line):
                for word in line.split(","):
                    add(word.strip())
                found = True
            else:
                m = _LIST_ITEM_RE.match(line)
                if m:
                    add(m.group(1), m.group(2) or "")
                    found = True
            i += 1
    return out


def derive_specs(bin_path: str, bin_name: str, help_of: Callable[[list[str]], str], *,
                 max_calls: int = MAX_HELP_CALLS, host: bool = False) -> list[dict]:
    """The CLI actions a command offers, from its own help (depth 2).

    *help_of(argv)* returns the help text of ``argv + ["--help"]`` (run in the
    box). Returns specs ``{"name","command","argv","bin","files","strings",
    "has_output_option","stdout_max","wall_s","allow_hosts"}``: ``argv`` is
    the pinned shape starting with the program's name, ``bin`` its full path
    in the box (see :func:`resolved_argv`). A program outside the tool's own
    folders gives no actions.

    *host*: a program the owner added herself on this computer
    (:mod:`puffpai.programs`): *bin_path* is its full path on this computer
    and *bin_name* its file name without the extension. These specs describe
    the program for the brain only; they are never run by
    :func:`resolved_argv` (which still takes box paths only)."""
    if host:
        if not (isinstance(bin_path, str) and os.path.isabs(bin_path) and _BIN_RE.match(
                bin_name or "") and os.path.splitext(os.path.basename(bin_path))[0].lower()
                == str(bin_name).lower()):
            return []
    else:
        m = _BIN_PATH_RE.match(bin_path or "")
        if not m or m.group(2) != bin_name:
            return []
    calls = {"n": 0}

    def ask(path: list[str]) -> Optional[dict]:
        if calls["n"] >= max_calls:
            return None
        calls["n"] += 1
        try:
            return parse_help(help_of([bin_path] + path))
        except Exception:
            return None

    specs: list[dict] = []

    def visit(path: list[str], info: Optional[dict], depth: int) -> None:
        if info is None or len(specs) >= MAX_ACTIONS:
            return
        subs = [s for s in info["subcommands"] if s not in SKIP_SUBCOMMANDS
                and _WORD_RE.match(s)]
        if subs and depth < 2:
            for sub in subs:
                visit(path + [sub], ask(path + [sub]), depth + 1)
            return
        pos = info["positionals"]
        files = [p for p in pos if p["file"]]
        if not files:
            return                          # an action works on a file from the folder
        strings = [p for p in pos if not p["file"]][:3]
        argv = [bin_name] + path
        for p in pos:
            if p["file"] or p in strings:
                argv.append("{" + p["name"] + "}")
        name = "_".join([bin_name] + path)
        name = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not name or not name[0].isalpha():
            name = "run_" + name
        name = name[:48].rstrip("_")
        if any(s["name"] == name for s in specs):
            return
        specs.append({
            "name": name, "command": " ".join([bin_name] + path),
            "argv": argv, "bin": bin_path, "files": {"file": {"from": "folder", "ext": info["exts"]}},
            "strings": [{"name": p["name"], "required": p["required"]} for p in strings],
            "has_output_option": info["has_output_option"],
            "stdout_max": STDOUT_MAX, "wall_s": WALL_S, "allow_hosts": []})

    visit([], ask([]), 0)
    return specs


__all__ = ["derive_specs", "list_subcommands", "parse_help"]
