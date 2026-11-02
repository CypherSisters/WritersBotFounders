"""A Word file in standard manuscript format, from the book's sections as they stand. No outside package: a .docx is a zip
of a little XML. It reads the text and writes a new file; it never changes anything it read.

The format, as commonly asked for: 12-point Courier, double spaced, one-inch margins, first lines indented, a title page
with the author's details and the word count rounded to the nearest hundred, each chapter on a new page, and the title and
page number at the top of every page after the first.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_FONT = '<w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:cs="Courier New"/><w:sz w:val="24"/>'


def _run(text: str) -> str:
    return f'<w:r><w:rPr>{_FONT}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(text: str = "", style: str = "", page_break: bool = False) -> str:
    props = {"body": '<w:spacing w:line="480" w:lineRule="auto" w:after="0"/><w:ind w:firstLine="720"/>',
             "center": '<w:spacing w:line="480" w:lineRule="auto" w:after="0"/><w:jc w:val="center"/>',
             "single": '<w:spacing w:line="240" w:lineRule="auto" w:after="0"/>', "": ""}[style]
    if page_break:
        props = "<w:pageBreakBefore/>" + props
    return f"<w:p><w:pPr>{props}</w:pPr>{_run(text) if text else ''}</w:p>"


def _clean(text: str) -> list[str]:
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]


def build(book, author: str = "") -> tuple[bytes, str]:
    """(the .docx bytes, its plain name). ``author``: what she typed for the title page, her name and how to reach her."""
    total = sum(s.words for s in book.sections)
    rounded = max(100, round(total, -2))
    lines = [x.strip() for x in re.split(r"[,\n]", str(author or "")) if x.strip()] or ["Your name", "Your address", "Your email and phone"]
    surname = lines[0].split()[-1] if lines[0] != "Your name" else "Surname"
    body = [_para(x, "single") for x in lines]
    body.append(_para(f"About {rounded:,} words", "single"))
    body += [_para() for _i in range(12)]
    body += [_para(book.title, "center"), _para("by " + lines[0], "center")]
    last_head = None
    for s in book.sections:
        head = re.sub(r",\s*part\s+\d+\s*$", "", s.heading, flags=re.I)
        if head != last_head:
            body.append(_para(head, "center", page_break=True))
            last_head = head
        for p in _clean(s.text):
            if re.fullmatch(r"(\*\s*\*\s*\*|-{3,}|#|~)", p):
                body.append(_para("#", "center"))
            else:
                body.append(_para(p, "body"))
    sect = ('<w:sectPr><w:headerReference w:type="default" r:id="rId2"/><w:titlePg/>'
            '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_W} {_R}><w:body>{"".join(body)}{sect}</w:body></w:document>'
    short = " ".join(book.title.split()[:3])
    header = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {_W}><w:p><w:pPr><w:jc w:val="right"/></w:pPr>'
              f'{_run(f"{surname} / {short} / ")}<w:r><w:rPr>{_FONT}</w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
              f'<w:r><w:rPr>{_FONT}</w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              f'<w:r><w:rPr>{_FONT}</w:rPr><w:fldChar w:fldCharType="end"/></w:r></w:p></w:hdr>')
    types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
             '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
             '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("word/header1.xml", header)
    return out.getvalue(), f"{book.title}, submission copy"
