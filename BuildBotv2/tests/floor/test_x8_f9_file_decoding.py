"""Floor X8, F9 (lane L3 verifier item 1): a file is read the way it was written, or not indexed at all.

The bug: UTF-16 was tried before Windows-1252, so an even-length Windows-1252
file read as CJK, the key in it was never held, and the index gave it back
when its text was turned into UTF-16-LE bytes again.
"""

import codecs
import sqlite3

import pytest

from puffpai_core.zone1.classify import Binary, Garbled, Unsupported, classify, read_text
from puffpai_core.zone1.folders import Library
from z1kit import canary_key, make


def _even(data: bytes) -> bytes:
    return data if len(data) % 2 == 0 else data + b"."


def _index_bytes(state) -> bytes:
    db = sqlite3.connect(state / "index" / "knowledge.db")
    try:
        rows = [t for (t,) in db.execute("SELECT text FROM chunks")]
    finally:
        db.close()
    out = b""
    for t in rows:          # every way the stored text could hand a key back
        out += t.encode("utf-8") + t.encode("utf-16-le", "surrogatepass") + t.encode("utf-16-be", "surrogatepass")
    return out + (state / "index" / "knowledge.db").read_bytes()


def test_even_length_cp1252_file_is_read_as_cp1252_and_its_key_held(state):
    a, *_ = make(state)
    key = canary_key()
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    f = lib.data_folder / "café.txt"
    f.write_bytes(_even(("Café notes, £ prices. The key: " + key + " ok").encode("cp1252")))
    assert read_text(f).startswith("Café notes")
    report = lib.scan()
    assert report["indexed"] == 1 and report["held"] >= 1
    assert key.encode() not in _index_bytes(state)
    assert lib.search("prices")                                  # the rest is searchable as written


@pytest.mark.parametrize("bom,enc", [(codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
                                     (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF32_LE, "utf-32-le")])
def test_files_with_a_byte_order_mark_are_supported(state, tmp_path, bom, enc):
    key = canary_key()
    f = tmp_path / "marked.txt"
    f.write_bytes(bom + ("PowerShell export. key " + key + " end").encode(enc))
    text = read_text(f)
    assert text.startswith("PowerShell export") and key in text
    assert "secret.api_key" in {h.label for h in classify(file=f).hits}


def test_utf16_without_a_mark_is_never_guessed(tmp_path):
    f = tmp_path / "nomark.txt"
    f.write_bytes(("no mark here " + canary_key()).encode("utf-16-le"))
    with pytest.raises(Unsupported):
        read_text(f)


def test_a_false_utf16_mark_on_cp1252_bytes_is_read_the_windows_way(state):
    a, *_ = make(state)
    key = canary_key()
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    f = lib.data_folder / "spoof.txt"
    f.write_bytes(_even(b"\xff\xfe" + ("notes about the shed. key " + key + " and more notes here").encode("cp1252")))
    assert key in read_text(f)                                    # the Windows-1252 reading, not CJK
    report = lib.scan()
    assert report["indexed"] == 1 and report["held"] >= 1
    assert key.encode() not in _index_bytes(state)
    assert lib.search("shed")


def test_replacement_and_control_junk_is_garbled(tmp_path):
    f = tmp_path / "junk.txt"
    f.write_text("ok " + "�\x01" * 50 + " end", encoding="utf-8")
    with pytest.raises((Garbled, Binary)):                       # either refusal: not indexed, said why
        read_text(f)
    g = tmp_path / "marks.txt"
    g.write_text("ok " + "�" * 60 + " end", encoding="utf-8")
    with pytest.raises(Garbled):
        read_text(g)


def test_real_cjk_in_utf8_stays_readable(tmp_path):
    f = tmp_path / "cn.txt"
    f.write_text("我的笔记。天气很好。" * 50, encoding="utf-8")
    assert read_text(f).startswith("我的")


def test_a_few_control_characters_are_fine(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("\x1b[32mstarted\x1b[0m server on port 8420 at noon\nall good\x07\n" * 20, encoding="utf-8")
    assert "started" in read_text(f)


def test_cp1252_spaces_after_a_coincidental_ff_fe_are_not_read_as_utf16(state):
    """Re-check item 2: mostly spaces after FF FE read as U+2020 and passed the garbage check."""
    a, *_ = make(state)
    key = canary_key()
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    body = ("    " * 10 + "x\n") * 30 + "k " + key + " "
    (lib.data_folder / "notes.txt").write_bytes(_even(b"\xff\xfe" + body.encode("cp1252")))
    report = lib.scan()
    assert report["indexed"] == 1 and report["held"] >= 1
    assert key.encode() not in _index_bytes(state)


def test_a_genuine_utf16_file_still_indexes_as_utf16(state):
    a, *_ = make(state)
    key = canary_key()
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    text = "Tomato soup with basil. key " + key + " and garlic."
    (lib.data_folder / "export.txt").write_bytes(codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
    assert lib.scan()["indexed"] == 1
    assert lib.search("basil") and key.encode() not in _index_bytes(state)


def _random_no_nul():
    import os
    return bytes(b for b in os.urandom(6000) if b != 0)


def _random_bytes():
    import os
    return os.urandom(4000)


@pytest.mark.parametrize("make_bytes", [_random_no_nul, _random_bytes, lambda: bytes(range(1, 32)) * 200])
def test_binary_saved_as_txt_is_not_indexed(state, make_bytes):
    """Re-check item 3: random binary saved as .txt was read as latin-1 and indexed."""
    a, *_ = make(state)
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    (lib.data_folder / "blob.txt").write_bytes(make_bytes())
    report = lib.scan()
    assert report["indexed"] == 0
    assert report["not_indexed"][0]["reason"] == "This looks like a binary file, so it wasn't indexed."
