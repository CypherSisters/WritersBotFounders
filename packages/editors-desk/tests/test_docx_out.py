"""docx_out.py: a Word copy keeps her italics and bold, in standard manuscript format and as a plain reading copy."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))
import docx_out  # noqa: E402

docx = pytest.importorskip("docx")


class Sec:
    def __init__(self, heading: str, text: str) -> None:
        self.heading, self.text = heading, text
        self.words = len(text.split())


class FakeBook:
    title = "Storm Harbor"

    def __init__(self) -> None:
        self.sections = [
            Sec("Chapter One", "She said *no* and **meant** it, ***really***.\n\n* * *\n\n2 * 3 * 4 is a sum."),
            Sec("Chapter One, part 2", "Already plain text, nothing to mark."),
            Sec("Chapter Two", "The *Ferry*\u2014a name\u2014sailed.\nA second line in the *same* paragraph."),
        ]


def runs_of(doc):
    """(text, italic, bold) of every run with words, in order, joined across paragraphs."""
    return [(r.text, bool(r.italic), bool(r.bold)) for p in doc.paragraphs for r in p.runs if r.text]


def body_paras(doc):
    return [p for p in doc.paragraphs if p.text.strip()]


def check_marks(doc):
    got = runs_of(doc)
    assert ("no", True, False) in got and ("meant", False, True) in got and ("really", True, True) in got
    assert ("Ferry", True, False) in got and ("same", True, False) in got
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "*no*" not in text and "**meant**" not in text and "***" not in text.replace("* * *", "")
    assert "2 * 3 * 4 is a sum." in text                                  # her own lone stars stay
    assert "Already plain text, nothing to mark." in text
    assert "The Ferry\u2014a name\u2014sailed. A second line in the same paragraph." in text


def test_standard_manuscript_format_keeps_italic_and_bold():
    raw, name = docx_out.build(FakeBook(), "Ada Quill, 1 Harbor Road, ada@example.com")
    assert name == "Storm Harbor, submission copy"
    doc = docx.Document(io.BytesIO(raw))
    check_marks(doc)
    assert all(r.font.name == "Courier New" for p in doc.paragraphs for r in p.runs if r.text)
    texts = [p.text for p in doc.paragraphs]
    assert texts.count("Chapter One") == 1 and "Chapter Two" in texts and "#" in texts      # a scene break as #
    assert doc.sections[0].different_first_page_header_footer
    assert "Quill / Storm Harbor /" in doc.sections[0].header.paragraphs[0].text
    assert "About 100 words" in texts


def test_plain_reading_copy():
    raw, name = docx_out.build(FakeBook(), "Ada Quill, 1 Harbor Road", plain=True)
    assert name == "Storm Harbor, reading copy"
    doc = docx.Document(io.BytesIO(raw))
    check_marks(doc)
    paras = body_paras(doc)
    assert paras[0].text == "Storm Harbor" and paras[1].text == "by Ada Quill"
    heads = [p for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert [p.text for p in heads] == ["Chapter One", "Chapter Two"]
    styles = zipfile.ZipFile(io.BytesIO(raw)).read("word/styles.xml").decode()
    assert "<w:pageBreakBefore/>" in styles.split('w:styleId="Heading1"')[1]                # each on a new page
    for p in paras[2:]:
        if p.style.name == "Heading 1":
            continue
        assert all(r.font.name == "Times New Roman" and r.font.size.pt == 12 for r in p.runs if r.text)
        assert p.paragraph_format.line_spacing == pytest.approx(1.15)
        if p.text == "* * *":
            assert p.alignment == 1                                            # centered, as it is
        else:
            assert p.paragraph_format.first_line_indent > 0
    assert "* * *" in [p.text for p in paras]
    assert not doc.sections[0].header.paragraphs or not "".join(x.text for x in doc.sections[0].header.paragraphs)
    names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
    assert "word/header1.xml" not in names


def test_a_section_with_raw_text_gives_its_marks():
    """book.Section offers ``raw`` (the file, marks and all) beside ``text`` (the words alone): the copy uses raw."""
    s = Sec("Chapter One", "She said no.")
    s.raw = "She said *no*."

    class Two:
        title = "Two"
        sections = [s]

    for plain in (False, True):
        doc = docx.Document(io.BytesIO(docx_out.build(Two(), "Ada Quill", plain=plain)[0]))
        assert ("no", True, False) in runs_of(doc)


def test_with_the_real_book(tmp_path):
    """A shelf folder read by book.Book, as engine.py hands it over."""
    from book import Book
    folder = tmp_path / "Real"
    (folder / "sections").mkdir(parents=True)
    (folder / "sections" / "01 - One.md").write_text("# One\n\nShe said *no* and **meant** it.\n", encoding="utf-8")
    (folder / "manuscript.json").write_text('{"title": "Real", "sections": [{"n": 1, "heading": "One", '
                                            '"file": "01 - One.md"}]}', encoding="utf-8")
    for plain in (False, True):
        doc = docx.Document(io.BytesIO(docx_out.build(Book(folder), "", plain=plain)[0]))
        got = runs_of(doc)
        assert ("no", True, False) in got and ("meant", False, True) in got


def test_plain_with_no_name_and_text_with_no_marks():
    class Bare:
        title = "Bare & Plain <1>"
        sections = [Sec("Whole manuscript", "Just words.")]

    for plain in (False, True):
        raw, _n = docx_out.build(Bare(), "", plain=plain)
        doc = docx.Document(io.BytesIO(raw))
        assert "Bare & Plain <1>" in [p.text for p in doc.paragraphs]
        assert ("Just words.", False, False) in runs_of(doc)
