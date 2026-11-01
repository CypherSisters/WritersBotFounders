"""The release-gate scan: exits 1 on a planted hit (docs/ included), 0 on a clean tree."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release_name_scan.py"


def _run(folder):
    return subprocess.run([sys.executable, str(SCRIPT), str(folder)], capture_output=True, text=True)


def test_planted_hit_in_docs_fails(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "n.md").write_text("ok\nby " + "".join(["Rach", "el"]) + "\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 1 and "docs/n.md:2" in r.stdout


def test_file_name_hit_fails(tmp_path):
    (tmp_path / ("puff-" + "".join(["Rach", "el"]) + ".webp")).write_bytes(b"\0\1")
    r = _run(tmp_path)
    assert r.returncode == 1 and "file name" in r.stdout


def test_local_words_file(tmp_path):
    (tmp_path / "a.txt").write_text("hello zorblax\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0
    (tmp_path / "release_names.local.txt").write_text("Zorblax\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 1 and "a.txt:1" in r.stdout


def test_clean_tree_passes(tmp_path):
    (tmp_path / "a.txt").write_text("nothing to see\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0 and r.stdout == ""


def _w(*parts):
    return "".join(parts)


def test_machine_ids_fail_in_text_and_file_names(tmp_path):
    samples = [_w("sem", "pe"), _w("ada", "ra"), _w("bright", "on", "early"), _w("192.", "168.", "50.7")]
    for i, w in enumerate(samples):
        (tmp_path / f"t{i}.txt").write_text("ok\nx " + w + "\n", encoding="utf-8")
        (tmp_path / f"n-{w}.bin").write_bytes(b"\0\1")
    r = _run(tmp_path)
    assert r.returncode == 1
    for i in range(len(samples)):
        assert f"t{i}.txt:2" in r.stdout
    assert r.stdout.count("file name") == len(samples)


def test_other_private_range_passes(tmp_path):
    (tmp_path / "a.txt").write_text("host 10.0.0.5\n", encoding="utf-8")
    assert _run(tmp_path).returncode == 0
