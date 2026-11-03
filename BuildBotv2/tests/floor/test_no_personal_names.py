"""Floor: the owner is never named in anything tracked, docs/ included (file names count)."""
import hashlib
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BAD = {
    "972964b66bdfe6b5b181c5112a6a0470204f64661ee7e3efb9aab0ce3cc403ff",
    "af7c752bf81f575efe5ac39f7ab8c61a3668ff0cf7e950114194fa5267cc830a",
    "9aacbb123a7eed8a9dc1b014c0a22cdc569065021a70fc9b2b69225c02ce6231",
    "ad98f7015eb42221754f49b65d658c2667b1daedd518feb7dbee182c5c96781a",
    "3edf0c611b9ec71517e64829305f6fec7eb7e34eedcb2c6249d20263d45f024b",
}
NET_HASH = "4946358a3a79887b1390685855327cd17e56a9eab6979d852830aa1b678e8439"
WORD = re.compile(r"[A-Za-z]+")
NET = re.compile(r"\d{1,3}(\.\d{1,3}){2,3}")


def _net_bad(text):
    return any(hashlib.sha256(".".join(m.group().split(".")[:3]).encode()).hexdigest() == NET_HASH
               for m in NET.finditer(text))


def _bad(text):
    return _net_bad(text) or any(hashlib.sha256(w.lower().encode()).hexdigest() in BAD for w in WORD.findall(text))


def find_hits(root, rels):
    hits = []
    for rel in rels:
        if _bad(rel):
            hits.append(f"{rel}: file name")
        path = Path(root) / rel
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if _bad(line):
                hits.append(f"{rel}:{n}")
    return hits


def tracked_files(root):
    """Paths in the git index (read directly: tests may not start git)."""
    dot = Path(root) / ".git"
    if dot.is_file():
        gitdir = Path(root) / dot.read_text(encoding="utf-8").split(":", 1)[1].strip()
    else:
        gitdir = dot
    if not (gitdir / "index").is_file():
        pytest.skip("no git index here (an export with nothing tracked), so there is no list of tracked files to read")
    data = (gitdir / "index").read_bytes()
    sig, version, count = struct.unpack(">4sII", data[:12])
    assert sig == b"DIRC" and version == 2
    pos, out = 12, []
    for _ in range(count):
        end = data.index(b"\0", pos + 62)
        out.append(data[pos + 62:end].decode("utf-8"))
        pos += (62 + (end - pos - 62) + 8) // 8 * 8
    return out


def test_no_personal_names_in_tracked_files():
    rels = tracked_files(ROOT)
    assert len(rels) > 100
    assert find_hits(ROOT, rels) == []


def test_the_scan_catches_a_planted_name(tmp_path):
    planted = "hello " + "".join(["rach", "el"]).title() + " there"
    (tmp_path / "a.txt").write_text("fine\n" + planted + "\n", encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing here\n", encoding="utf-8")
    (tmp_path / ("x-" + "".join(["beth", "any"]) + ".bin")).write_bytes(b"\0\1")
    hits = find_hits(tmp_path, ["a.txt", "clean.txt", "x-" + "".join(["beth", "any"]) + ".bin"])
    assert "a.txt:2" in hits and any(h.endswith(".bin: file name") for h in hits)
    assert not any(h.startswith("clean.txt") for h in hits)


def _w(*parts):
    return "".join(parts)


MACHINE_WORDS = [_w("sem", "pe"), _w("ada", "ra"), _w("bright", "on", "early")]


def test_machine_hashes_match_their_words():
    for w in MACHINE_WORDS:
        assert hashlib.sha256(w.encode()).hexdigest() in BAD
    assert hashlib.sha256((_w("192.", "168.", "50")).encode()).hexdigest() == NET_HASH


def test_machine_ids_caught_in_text_and_names(tmp_path):
    samples = MACHINE_WORDS + [_w("192.", "168.", "50.7"), _w("192.", "168.", "50")]
    for i, w in enumerate(samples):
        (tmp_path / f"t{i}.txt").write_text("ok\nhost " + w.title() + " here\n", encoding="utf-8")
        (tmp_path / f"n-{w}.bin").write_bytes(b"\0\1")
    rels = sorted(p.name for p in tmp_path.iterdir())
    hits = find_hits(tmp_path, rels)
    for i in range(len(samples)):
        assert f"t{i}.txt:2" in hits
    assert sum(h.endswith(".bin: file name") for h in hits) == len(samples)


def test_other_private_range_not_flagged(tmp_path):
    (tmp_path / "a.txt").write_text("host 10.0.0.5 and 192.168.1.9 and 1.2.3\n", encoding="utf-8")
    assert find_hits(tmp_path, ["a.txt"]) == []
