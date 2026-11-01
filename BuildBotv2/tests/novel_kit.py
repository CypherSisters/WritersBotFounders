"""Write a novel-sized .txt, .docx and .pdf into a folder, from scratch (no download). About 120,000 words.

Plain prose with named characters, plus what a real novel has that a privacy check might trip on: street addresses,
letters, birthdays, phone numbers in dialogue, an illness plot, dates, a school, a password in a spy subplot."""
import random
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

PEOPLE = ["Margaret Hale", "Thomas Ashby", "Eleanor Vance", "Samuel Oakes", "Ruth Calloway", "Daniel Pryce",
          "Clara Whitlock", "Henry Marsh", "Ida Bellamy", "Walter Finch", "Josephine Reyes", "Arthur Lund"]
PLAIN = [
    "{a} stood at the kitchen window for a long while before {b} came in, and neither of them said what they had come to say.",
    "\"You never told me,\" {b} said. {a} set the cup down and looked out over the wet fields. \"There was never a good day for it.\"",
    "By the time the rain stopped the road to the mill had gone soft, and {a} walked it anyway, carrying the letter inside a coat that had been {c}'s.",
    "It was the summer the river ran low, the summer {a} learned to keep accounts, and the summer {b} stopped coming to supper on Sundays.",
    "{a} had always believed that a house remembers. The stairs at the top of the hill remembered {b}, and groaned for no one else.",
    "\"Mrs. {last} will want an answer by morning,\" {b} said, and {a} laughed, though nothing about it was funny.",
    "The train was four hours late. {a} sat on a bench with a paper bag of plums, watching {c} pretend not to have seen them.",
    "Nobody in town spoke of the fire any more, which is how {a} knew they still thought about it every day.",
    "{b} wrote three pages and burned two. What was left said only that the orchard had come in well and that {a} should not trouble to visit.",
    "Winter came early that year. {a} chopped what wood there was, and {c} counted the jars in the cellar twice and did not say the number aloud.",
    "\"I knew your father,\" said {b}. \"I know you did,\" said {a}. \"That is the whole of the trouble between us.\"",
    "When the bells rang for the armistice, {a} was up a ladder in the barn, and stayed there until the sound had gone out of the valley.",
    "There was a photograph on the mantel of {c} at nineteen, squinting into the sun, and {a} turned it to the wall each night before bed.",
    "{a} read the will twice. Then {a} folded it along its old creases and asked {b} whether there was any coffee left.",
]
TRICKY = [
    "{a} had moved to 14 Larkspur Lane the spring after the funeral, and the letters followed from 212 Harbor Street for a year.",
    "\"Call me at 555-867-5309 if he comes back,\" {b} said, writing it on the inside of a matchbook.",
    "She was born on March 3, 1902, in the back room of the bakery, and {a} never let anyone forget it.",
    "{a} was diagnosed with cancer in the autumn, and told no one but {b}, who drove to the hospital every Tuesday.",
    "His mother had dementia by then, and asked {a} the same question at every meal.",
    "The doctor said {b} had a heart attack in the night, though {a} would not hear the word.",
    "{a} had gone to Lincoln Elementary with {b}, and then to Saint Mary's Academy until the money ran out.",
    "\"The password is swordfish,\" whispered {b}, \"and if you repeat it I will know.\"",
    "My name is {a}, and I am writing this from Oak Ridge, Tennessee 37830, where nobody knows me.",
    "{c} sent a cable to Portland, Oregon 97201 on June 14, 1931, and waited three weeks for no reply.",
    "She was pregnant again that winter. {a} said nothing, and {b} said too much.",
    "He had depression, the doctor said, which in those days {a} took to mean he was tired.",
    "They found the account at First National, account number 4471-2290-118, in {c}'s name and no one else's.",
    "Write to me at clara.whitlock@example.com, the note said, an address that could not have existed then, which was how {a} knew it was a joke.",
    "{a} took the 7:15 to Union Station, 800 North Alameda Street, and sat with the suitcase on both knees.",
    "Her birthday was May 9, and every May 9 {b} left lilacs on the step at 31 Quarry Road.",
]


def novel(words: int, seed: int = 7) -> str:
    rng = random.Random(seed)
    out, n, chapter = [], 0, 0
    while n < words:
        chapter += 1
        out.append(f"Chapter {chapter}")
        for _ in range(rng.randint(30, 60)):
            sents = []
            for _ in range(rng.randint(3, 7)):
                a, b, c = rng.sample(PEOPLE, 3)
                pool = TRICKY if rng.random() < 0.04 else PLAIN
                sents.append(rng.choice(pool).format(a=a, b=b, c=c, last=b.split()[-1]))
            para = " ".join(sents)
            out.append(para)
            n += len(para.split())
    return "\n\n".join(out)


def write_docx(text: str, path: Path) -> None:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{escape(p)}</w:t></w:r></w:p>' for p in text.split("\n\n"))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           + body + "</w:body></w:document>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        z.writestr("word/document.xml", doc)


def write_pdf(text: str, path: Path) -> None:
    """A real text PDF, hand-made: about 45 lines a page in Helvetica."""
    lines, width = [], 88
    for para in text.split("\n\n"):
        line = ""
        for w in para.split():
            if len(line) + len(w) + 1 > width:
                lines.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        lines += [line, ""]
    pages = [lines[i:i + 45] for i in range(0, len(lines), 45)]
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for page in pages:
        ops = ["BT /F1 10 Tf 13 TL 60 740 Td"]
        for ln in page:
            safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    b"/Contents %d 0 R >>" % len(objs))
        kids.append(len(objs))
    objs[1] = b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), b" ".join(b"%d 0 R" % k for k in kids))
    out, at = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        at.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for a in at:
        out += b"%010d 00000 n \n" % a
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(bytes(out))


if __name__ == "__main__":
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    text = novel(int(sys.argv[2]) if len(sys.argv) > 2 else 120000)
    (folder / "novel.txt").write_text(text, encoding="utf-8")
    write_docx(text, folder / "novel.docx")
    write_pdf(text, folder / "novel.pdf")
    print(len(text.split()), "words", len(text), "chars")
    for name in ("novel.txt", "novel.docx", "novel.pdf"):
        print(name, (folder / name).stat().st_size)
