"""PDFs made at test time, for pdf_text.py's tests. The same file sits in Manuscript's and Document convert's tests.
The novel is set by hand, line by line, so each test knows the exact paragraph it should get back. Needs reportlab;
the tests that call these skip without it. Pictures and locks use pypdf alone."""

from __future__ import annotations

import io

W, H = 432, 648
L, TOP = 54, 590
SIZE, LEAD = 11, 15
INDENT = 18

# The pages of the novel. Each is (kind, words). "line" is flush left, "first" is indented, "gap" a space in points.
FRONT = [("big", "The Salt Ledger"), ("center", "A novel")]
PAGE_2 = [
    ("chapter", "Chapter 1"),
    ("first", "The ferry left at dawn and nobody on the harbour came down"),
    ("line", "to see it go. She counted the gulls from the end of the break-"),
    ("line", "water and wrote the number in the blue ledger her mother kept"),
    ("line", "on the shelf above the stove, between the tide tables and a"),
    ("line", "certificate of bap-"),
    ("line", "tism nobody had asked to see in twenty years."),
    ("first", "\u201cNot tonight,\u201d she said."),
    ("first", "\u201cIt\u2019s nobody\u2019s now,\u201d said Mr. B\u00e9ranger, and shut the caf\u00e9."),
    ("first_mixed", [("Times-Roman", "Every morning she read "), ("Times-Italic", "The Harbour Times"),
                     ("Times-Roman", " at the long table by")]),
    ("line", "the window, and every morning it told her the same well-"),
    ("line", "known story about the same Anglo-"),
    ("line", "Saxon stones on the same hill behind the church at Storm Point."),
    ("first", "The wind came off the water all the same, and it carried the"),
    ("line", "smell of tar and old rope up the lane to the last house, where"),
    ("line", "the lamp had been burning since before the flood. She stood"),
    ("line", "at the door a long while, her hand on the latch, and the rain"),
    ("line", "came on, and still she did not go in, because her brother was"),
    ("line", "at the table and she could not yet say what she had to say to"),
]
PAGE_3 = [
    ("line", "him. When she did go in at last he was asleep with his head on"),
    ("line", "his arms, and the unforgiv-"),
    ("line", "able letter lay open by his hand."),
    ("first", "She read it standing. It was short, and it was not kind, and it"),
    ("line", "was signed with a name she had not seen since the first harvest."),
    ("center", "* * *"),
    ("first", "By morning the rain had gone. She took the boat out alone and"),
    ("line", "rowed as far as the second buoy, then let it drift while she ate"),
    ("line", "her bread and looked at the town the way a stranger would."),
    ("gap", 40),
    ("line", "Three days later the letter was gone from the table, and neither"),
    ("line", "of them spoke of it, not then and not at the service on Sunday."),
    ("first", "\u201cWhose boat is that?\u201d she asked, and waited."),
    ("first", "He shook his head. The bells had started. Down at the water the"),
    ("line", "ferryman was tying off the morning boat and looking up at them"),
    ("line", "as if he knew the whole of it, and perhaps he did, for the"),
]
PAGE_4 = [
    ("line", "ferryman knew most things that happened on the harbour side."),
    ("plain_heading", "Chapter 3"),
    ("first", "Nobody on the harbour remembered the winter the same way. The"),
    ("line", "schoolteacher said it began in the first week of November, and"),
    ("line", "the apothecary said it had never truly ended at all."),
    ("first", "She kept her own count in the ledger, one line for every day the"),
    ("line", "ice held, and when she added it up in March it came to more than"),
    ("line", "either of them had guessed. She told neither of them that."),
    ("first", "\u201cYes,\u201d her brother said, when she showed him the page."),
    ("first", "\u201cThat\u2019s all? Yes?\u201d"),
    ("first", "\u201cWhat else is there to say about the winter now it is done?\u201d"),
]
PAGE_5 = [
    ("roman", "IV."),
    ("first", "The mainland boat came in on the first warm day of the year with"),
    ("line", "eleven passengers, nine of them strangers, and the harbour turned"),
    ("line", "out to look at them as it always did. A woman in a grey coat asked"),
    ("line", "the way to the last house, and nobody thought to ask her why."),
    ("first", "By noon the whole town knew her name, and by evening it knew"),
    ("line", "what she had come for, and that was the end of the quiet year."),
]
PAGES = [PAGE_2, PAGE_3, PAGE_4, PAGE_5]

FRONT_TEXT = ["The Salt Ledger", "A novel"]
PARAGRAPHS = {
    "ferry": "The ferry left at dawn and nobody on the harbour came down to see it go. She counted the gulls from the end "
             "of the breakwater and wrote the number in the blue ledger her mother kept on the shelf above the stove, "
             "between the tide tables and a certificate of baptism nobody had asked to see in twenty years.",
    "tonight": "\u201cNot tonight,\u201d she said.",
    "cafe": "\u201cIt\u2019s nobody\u2019s now,\u201d said Mr. B\u00e9ranger, and shut the caf\u00e9.",
    "times": "Every morning she read The Harbour Times at the long table by the window, and every morning it told her the "
             "same well-known story about the same Anglo-Saxon stones on the same hill behind the church at Storm Point.",
    "wind": "The wind came off the water all the same, and it carried the smell of tar and old rope up the lane to the last "
            "house, where the lamp had been burning since before the flood. She stood at the door a long while, her hand "
            "on the latch, and the rain came on, and still she did not go in, because her brother was at the table and "
            "she could not yet say what she had to say to him. When she did go in at last he was asleep with his head on "
            "his arms, and the unforgivable letter lay open by his hand.",
    "short": "She read it standing. It was short, and it was not kind, and it was signed with a name she had not seen since "
             "the first harvest.",
    "rain": "By morning the rain had gone. She took the boat out alone and rowed as far as the second buoy, then let it "
            "drift while she ate her bread and looked at the town the way a stranger would.",
    "three": "Three days later the letter was gone from the table, and neither of them spoke of it, not then and not at the "
             "service on Sunday.",
    "whose": "\u201cWhose boat is that?\u201d she asked, and waited.",
    "bells": "He shook his head. The bells had started. Down at the water the ferryman was tying off the morning boat and "
             "looking up at them as if he knew the whole of it, and perhaps he did, for the ferryman knew most things that "
             "happened on the harbour side.",
}


def _canvas(buf):
    from reportlab.pdfgen import canvas
    return canvas.Canvas(buf, pagesize=(W, H))


def _draw(c, kind, words, y):
    """Draws one entry, answers the next line's height."""
    if kind == "gap":
        return y - words
    if kind == "big":
        c.setFont("Times-Bold", 26)
        c.drawCentredString(W / 2, 450, words)
        return y
    if kind == "center":
        c.setFont("Times-Roman", SIZE)
        c.drawCentredString(W / 2, y if words == "* * *" else 410, words)
        return y - LEAD
    if kind == "chapter":
        c.setFont("Times-Bold", 18)
        c.drawCentredString(W / 2, y, words)
        return y - 2.5 * LEAD
    if kind == "plain_heading":
        c.setFont("Times-Roman", SIZE)
        c.drawString(L, y - LEAD, words)
        return y - 3 * LEAD
    if kind == "roman":
        c.setFont("Times-Roman", SIZE)
        c.drawCentredString(W / 2, y, words)
        return y - 2 * LEAD
    x = L + INDENT if kind.startswith("first") else L
    t = c.beginText(x, y)
    for font, bit in (words if kind == "first_mixed" else [("Times-Roman", words)]):
        t.setFont(font, SIZE)
        t.textOut(bit)
    c.drawText(t)
    return y - LEAD


def novel(heads: bool = True) -> bytes:
    """Five pages: a title page and four of text, with a running head that alternates and a page number on each."""
    buf = io.BytesIO()
    c = _canvas(buf)
    y = TOP
    for kind, words in FRONT:
        y = _draw(c, kind, words, y)
    c.showPage()
    for number, page in enumerate(PAGES, 2):
        if heads:
            c.setFont("Times-Roman", 9)
            if number > 2:
                c.drawCentredString(W / 2, H - 36, "THE SALT LEDGER" if number % 2 == 0 else "A Fine Blue Author")
            c.drawCentredString(W / 2, 34, str(number))
        y = TOP
        for kind, words in page:
            y = _draw(c, kind, words, y)
        c.showPage()
    c.save()
    return buf.getvalue()


def text_pages(pages: list[list[str]], size: float = SIZE, leading: float = LEAD, x: float = L) -> bytes:
    """Plain pages of flush lines, one list per page. An empty string is a blank line's space."""
    buf = io.BytesIO()
    c = _canvas(buf)
    for page in pages:
        y = TOP
        for line in page:
            if line:
                c.setFont("Times-Roman", size)
                c.drawString(x, y, line)
            y -= leading
        c.showPage()
    c.save()
    return buf.getvalue()


_WORDS = ("oak ash elm yew fir bay box fig gum may nut oat pea rye sage teak vine wort yam lime pine plum rose reed "
          "moss fern kelp sedge heath thorn").split()


def two_columns() -> bytes:
    """One page of two columns of short lines, the left column first in the file."""
    buf = io.BytesIO()
    c = _canvas(buf)
    c.setFont("Times-Roman", 9)
    for side, x in (("Left", 30), ("Right", 230)):
        y = 600
        for i in range(30):
            c.drawString(x, y, f"{side} column, line {_WORDS[i]}, in plain words here.")
            y -= 12
    c.showPage()
    c.save()
    return buf.getvalue()


def table_page() -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf)
    c.setFont("Times-Roman", 10)
    for row in range(8):
        for col, x in enumerate((54, 180, 300)):
            c.drawString(x, 600 - 16 * row, f"cell {row}-{col}")
    c.showPage()
    c.save()
    return buf.getvalue()


def _picture_page(writer) -> None:
    """A page that is one small grey picture, the way a scanner makes them. Built with pypdf alone."""
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject
    image = DecodedStreamObject()
    image.set_data(bytes([128]) * 64)
    image.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                  NameObject("/Width"): NumberObject(8), NameObject("/Height"): NumberObject(8),
                  NameObject("/ColorSpace"): NameObject("/DeviceGray"), NameObject("/BitsPerComponent"): NumberObject(8)})
    ref = writer._add_object(image)
    page = writer.add_blank_page(W, H)
    draw = DecodedStreamObject()
    draw.set_data(f"q {W} 0 0 {H} 0 0 cm /Im0 Do Q".encode())
    page[NameObject("/Contents")] = writer._add_object(draw)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): ref}),
                                                       NameObject("/ProcSet"): ArrayObject([NameObject("/ImageB")])})


def _written(writer) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def scanned(pages: int = 3) -> bytes:
    """Pictures of pages, with no text layer."""
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        _picture_page(w)
    return _written(w)


def mixed() -> bytes:
    """Three pages of text with a picture of a page between them: page 3 of 4 has no text."""
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(io.BytesIO(text_pages([
        ["The ferry left at dawn and nobody on the harbour came down to", "see it go, and she wrote the number of the gulls in her ledger."],
        ["The second morning was the same as the first had been, and she", "wrote that down as well before the rain came in from the sea."],
        ["The third morning there was no ferry at all, and nobody on the", "harbour could say why, and she left that line of the page empty."]])))
    w = PdfWriter()
    w.add_page(r.pages[0])
    w.add_page(r.pages[1])
    _picture_page(w)
    w.add_page(r.pages[2])
    return _written(w)


def hidden_text() -> bytes:
    """Text over a picture on every page, the way a scanner's text layer sits."""
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(io.BytesIO(mixed()))
    w = PdfWriter()
    picture = r.pages[2]
    for i in (0, 1, 3):
        w.add_page(r.pages[i])
        res = w.pages[-1]["/Resources"].get_object()
        from pypdf.generic import NameObject
        res[NameObject("/XObject")] = picture["/Resources"]["/XObject"].clone(w)
    return _written(w)


def empty() -> bytes:
    """Blank pages: no text and no pictures."""
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(W, H)
    w.add_blank_page(W, H)
    return _written(w)


def locked(user_password: str = "secret", algorithm: str = "RC4-128") -> bytes:
    """The novel, encrypted. With user_password "" it opens with no password, like a PDF that only limits printing."""
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(novel())))
    w.encrypt(user_password=user_password, owner_password="owner", algorithm=algorithm)
    return _written(w)
