"""Release gate: fail if a personal name appears anywhere in the tree.

Usage: python scripts/release_name_scan.py [FOLDER]   (default: the repo root)

Scans the WHOLE folder with no exemptions (docs/ included): every file name, and the text of every
file that is not binary. A dotted-number token whose first three parts match a stored network prefix also counts. Words are letters only, lower-cased, and are compared by sha256, so the
names are never written in this file. Extra words can go in an optional local file
``release_names.local.txt`` in the scanned folder (one word per line, never committed).
Only .git, .venv and __pycache__ are skipped. Prints file:line for each hit; exits 1 on any hit.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

HASHES = {
    "972964b66bdfe6b5b181c5112a6a0470204f64661ee7e3efb9aab0ce3cc403ff",
    "af7c752bf81f575efe5ac39f7ab8c61a3668ff0cf7e950114194fa5267cc830a",
    "9aacbb123a7eed8a9dc1b014c0a22cdc569065021a70fc9b2b69225c02ce6231",
    "ad98f7015eb42221754f49b65d658c2667b1daedd518feb7dbee182c5c96781a",
    "3edf0c611b9ec71517e64829305f6fec7eb7e34eedcb2c6249d20263d45f024b",
}
NET_HASH = "4946358a3a79887b1390685855327cd17e56a9eab6979d852830aa1b678e8439"
LOCAL_FILE = "release_names.local.txt"
SKIP_DIRS = {".git", ".venv", "__pycache__"}
WORD = re.compile(r"[A-Za-z]+")
NET = re.compile(r"\d{1,3}(\.\d{1,3}){2,3}")


def _hash(word: str) -> str:
    return hashlib.sha256(word.encode("utf-8")).hexdigest()


def _net_bad(text: str) -> bool:
    """True if a dotted-number token starts with the stored private network prefix."""
    return any(_hash(".".join(m.group().split(".")[:3])) == NET_HASH for m in NET.finditer(text))


def _bad_words(root: Path) -> set[str]:
    bad = set(HASHES)
    local = root / LOCAL_FILE
    if local.is_file():
        for line in local.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                bad.add(_hash(line))
    return bad


def scan(root: Path) -> list[str]:
    root = Path(root)
    bad = _bad_words(root)
    hits: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames) + list(dirnames):
            if _net_bad(name) or any(_hash(w.lower()) in bad for w in WORD.findall(name)):
                hits.append(f"{(Path(dirpath) / name).relative_to(root).as_posix()}:0: file name")
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel == LOCAL_FILE:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data:
                continue
            for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                if _net_bad(line) or any(_hash(w.lower()) in bad for w in WORD.findall(line)):
                    hits.append(f"{rel}:{n}")
    return hits


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent
    hits = scan(root)
    for h in hits:
        print(h)
    if hits:
        print(f"{len(hits)} hit(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
