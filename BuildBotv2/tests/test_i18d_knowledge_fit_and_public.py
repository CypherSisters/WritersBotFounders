"""Lane I18d: knowledge that is about the goal, and public text that is not the owner's data.

1. A fetched page is kept only when it shows the goal's own words, not just its topic's word (the owner's test3
   space build read albums called "Clarity" for "sky clarity"). Proved on that space build and on unrelated
   goals (garden, homework, book club, car care); the check names no field of its own.
2. Gutenberg is asked again with one of a topic's own words when the whole phrase finds no book; nothing but the
   topics' words is ever sent.
3. Text the Core's own fetch wrote is indexed whole and never sealed into the vault inbox; the owner's own files
   (anywhere, including the knowledge folder, and a fetched file she changed) keep the full privacy screen.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import urllib.parse
from contextlib import closing

import pytest

from puffpai_core.zone1 import fetched
from puffpai_core.zone1.folders import PUBLIC_REASON, Library
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from test_i11_knowledge import Web, _fake_world, _yes_and_build, real_core  # noqa: F401 - the fixture
from z1kit import make


@pytest.fixture(autouse=True)
def _two_article_sources(monkeypatch):
    """These tests are about how an article source and a book source are read: only those two rows here. Every other
    row has its own tests (``test_sources_knowledge.py``, ``test_sources_readers.py``)."""
    from puffpai_core.zone3.toolsmith import find
    real = find.sources

    def only_two():
        got = real()
        got["fetch"] = {k: v for k, v in got["fetch"].items() if k in ("wikipedia", "gutenberg")}
        return got
    monkeypatch.setattr(find, "sources", only_two)

# -- the owner's test3 space build (goal and topics as her bot's brain gave them) ------------------------------
SPACE_GOAL = ("I'd like to build a space bot that knows everything about space, can track cool celecstial events, "
              "show me when I can go outside and see celestial events, knows where the ISS is, shows me cool new "
              "pictures, and helps me learn about space")
SPACE_TOPICS = ["celestial events", "sky clarity", "iss orbit", "space telescope", "astronomy photography"]

#: topic -> [(title, short description, the article's start)], in Wikipedia's order. Her test3 titles; the
#: starts are short summaries of the real ones, with the words that decide.
SPACE_PAGES = {
    "celestial events": [
        ("1561 celestial phenomenon over Nuremberg", "", "A mass sighting of objects in the sky over Nuremberg, "
         "printed in a broadsheet as a celestial event seen at dawn."),
        ("Untamed (TV series)", "American crime drama television series", "Untamed is a crime drama television "
         "series set in Yosemite National Park, released on Netflix in 2025."),
        ("Astrotourism", "", "Travel to places with dark skies to watch celestial events such as eclipses, and to "
         "visit observatories for astronomy."),
        ("Vishvavasu", "Gandharva in Hinduism", "A gandharva of Hindu mythology, a class of celestial beings, who "
         "leads celestial musicians at celestial events in the courts of the gods and in space."),
        ("Observatory", "", "A place for observing celestial events in the sky and in space with telescopes."),
        ("Naked eye", "", "Seeing celestial events in the sky with no telescope, as in early astronomy."),
        ("Sun cross", "", "A symbol tied to the sun in early astronomy and to celestial events like the solstice."),
    ],
    "sky clarity": [
        ("Clarity (Zedd album)", "2012 studio album by Zedd", "Clarity is the debut studio album by electronic "
         "music producer Zedd, released in 2012; its singles include Fall into the Sky."),
        ("Clarity (Jimmy Eat World album)", "1999 studio album by Jimmy Eat World", "Clarity is the third studio "
         "album by the American rock band Jimmy Eat World, released in 1999."),
        ("Saleka", "American singer", "Saleka is an American singer and songwriter; her first record was called "
         "Clarity and she sang under a clear sky at her first shows."),
        ("Sky Cinema", "British film service", "Sky Cinema is a British subscription film service that shows "
         "motion pictures and live film events on Sky."),
        ("PRS Silver Sky", "Electric guitar", "The Silver Sky is an electric guitar made by PRS Guitars, prized "
         "for the clarity of its tone."),
        ("Tal Keinan", "Israeli-American businessman", "Tal Keinan is a businessman and author who founded the "
         "investment firm Clarity Capital."),
        ("Southern Highlands, Nevada", "Planned community in Nevada", "A planned community in the Las Vegas "
         "Valley, with golf, open space and a clear sky over the hills."),
        ("Marisa Abela", "British actress", "Marisa Abela (born 7 December 1996) is a British actress who had a "
         "role in the Sky One thriller COBRA."),
        ("Astronomical seeing", "", "How much the air blurs celestial objects seen through a telescope; a clear, "
         "steady sky makes for good seeing in astronomy."),
        ("Light pollution", "", "Too much light at night, which hides celestial events and the stars of the night "
         "sky from astronomy."),
    ],
    "iss orbit": [
        ("International Space Station", "", "The International Space Station (ISS) is a large space station in "
         "low Earth orbit."),
        ("Uncrewed spaceflights to the International Space Station", "", "Cargo flights to the ISS in space with "
         "no crew aboard."),
        ("Cygnus (spacecraft)", "", "An American cargo spacecraft that carries supplies to the ISS in space."),
        ("Antares (rocket)", "American launch vehicle", "A rocket that launched Cygnus cargo spacecraft to the "
         "International Space Station (ISS)."),
        ("Cupola (ISS module)", "", "A module of the ISS with seven windows for watching Earth and celestial "
         "objects in space."),
        ("Space station", "", "A spacecraft that stays in orbit for crews to live on, like the ISS in space."),
        ("Nauka (ISS module)", "", "A Russian module of the ISS, launched into space in 2021."),
        ("Orbital Sciences Corporation", "American company", "An American company that made satellites and "
         "rockets for space launch."),
    ],
    "space telescope": [
        ("Space telescope", "", "A telescope in outer space for astronomy, above the blur of the sky."),
        ("Hubble Space Telescope", "", "A space telescope launched into low Earth orbit in 1990 for astronomy."),
        ("James Webb Space Telescope", "", "A space telescope for infrared astronomy, in orbit around a point "
         "beyond the Moon."),
        ("Nancy Grace Roman Space Telescope", "", "A NASA space telescope for infrared astronomy, to go into "
         "orbit."),
        ("Euclid (space telescope)", "", "A space telescope of the European Space Agency in orbit since 2023."),
        ("Spitzer Space Telescope", "", "An infrared space telescope for astronomy that left orbit in 2020."),
        ("Telescope", "", "An instrument that makes far objects look near, used in astronomy for celestial "
         "objects."),
        ("List of space telescopes", "", "Space telescopes for astronomy, listed by what they see and their "
         "orbit."),
    ],
    "astronomy photography": [
        ("Photography", "Art of creating images", "Photography is the art of making images by recording light "
         "with a camera."),
        ("Backscatter", "Reflection of waves", "In physics, backscatter is the reflection of waves back toward "
         "their source; it matters in astronomy, photography and radar from space."),
        ("History of astronomy", "", "The study of the sky and space from the first star charts to the "
         "telescope."),
        ("List of photography awards", "", "A list of awards for photography and photographers."),
        ("Astronomy Photographer of the Year", "", "A yearly contest for photographs of the night sky and "
         "space."),
        ("Astronomy", "", "The science of celestial objects and events in the sky."),
        ("Astrophotography", "", "Photography of celestial objects and events in the night sky through a "
         "telescope."),
        ("Observational astronomy", "", "Astronomy that watches celestial objects in space with a telescope."),
    ],
}
#: Gutenberg: search words -> [(ebook number, title, the book's first pages)].
SPACE_BOOKS = {
    "astronomy photography": [
        (53396, "Practical Talks by an Astronomer", "Talks on the sun, the moon and the planets, with notes on "
         "how a telescope shows the sky."),
        (53542, "On the Construction of a Silvered Glass Telescope", "How to grind and silver a glass mirror for "
         "a telescope to photograph the moon and celestial objects."),
    ],
    "telescope": [(1001, "The Telescope", "The telescope in astronomy: how it shows the celestial objects of "
                   "the night sky.")],
    "celestial": [(1002, "The Celestial Railroad", "A dream of pilgrims who ride a railroad from the City of "
                   "Destruction to the Celestial City.")],
}
#: What her test3 build read before this lane (39 articles, 2 books), and what is kept now.
BEFORE = [t for pages in SPACE_PAGES.values() for t, _d, _s in pages if t not in ("Astronomical seeing",
                                                                                  "Light pollution")]
LEFT_OUT = {"Untamed (TV series)", "Clarity (Zedd album)", "Clarity (Jimmy Eat World album)", "Saleka",
            "PRS Silver Sky", "Tal Keinan", "Southern Highlands, Nevada", "Marisa Abela",
            "Orbital Sciences Corporation", "Photography", "Backscatter", "List of photography awards"}
AFTER = [t for t in BEFORE if t not in LEFT_OUT] + ["Astronomical seeing", "Light pollution"]


class SpaceWeb(Web):
    """Recorded answers for the space build: its own titles, short descriptions and books."""

    def __init__(self, pages=SPACE_PAGES, books=SPACE_BOOKS) -> None:
        super().__init__()
        self.pages = pages
        self.books = books
        self.about = {t: (d, s) for rows in pages.values() for t, d, s in rows}
        self.titles = {topic: [t for t, _d, _s in rows] for topic, rows in pages.items()}
        self.book_text = {n: (title, text) for rows in books.values() for n, title, text in rows}

    def __call__(self, url, headers):
        u = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(u.query)
        if u.hostname == "en.wikipedia.org" and "titles" in q:
            self.calls.append((url, dict(headers)))
            title = q["titles"][0]
            about, start = self.about[title]
            page = {"pageid": abs(hash(title)) % 10 ** 7 + 1, "title": title, "extract": (start + " ") * 6,
                    "revisions": [{"revid": 1}], "pageprops": {"wikibase-shortdesc": about} if about else {},
                    "canonicalurl": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))}
            return 200, json.dumps({"query": {"pages": [page]}}).encode()
        if u.hostname == "www.gutenberg.org" and u.path == "/ebooks/search.opds/":
            self.calls.append((url, dict(headers)))
            rows = self.books.get(q["query"][0], [])
            entries = "".join(f"<entry><title>{t}</title><id>https://www.gutenberg.org/ebooks/{n}.opds</id></entry>"
                              for n, t, _x in rows)
            return 200, f"<feed>{entries}</feed>".encode()
        m = re.fullmatch(r"/cache/epub/(\d+)/pg\1\.txt", u.path or "")
        if u.hostname == "www.gutenberg.org" and m:
            self.calls.append((url, dict(headers)))
            _title, text = self.book_text[int(m.group(1))]
            body = (f"*** START OF THE PROJECT GUTENBERG EBOOK {m.group(1)} ***\n" + (text + "\n") * 40
                    + "*** END OF THE PROJECT GUTENBERG EBOOK ***\n")
            return 200, body.encode()
        return super().__call__(url, headers)


def _kept(state, source):
    meta = fetched.root(state) / source / "meta.json"
    if not meta.is_file():
        return []
    items = json.loads(meta.read_text(encoding="utf-8"))["items"].values()
    return sorted(items, key=lambda v: v["fetched_at"])


def _sent_words(web):
    return [v for u, _h in web.calls for k, vs in urllib.parse.parse_qs(urllib.parse.urlsplit(u).query).items()
            if k in ("srsearch", "query") for v in vs]


# -- 1. the space build: about the goal, not just the topic word -----------------------------------------------
def test_the_space_build_keeps_pages_about_the_goal_and_leaves_out_the_rest(tmp_path, state):
    web = SpaceWeb()
    w = _fake_world(tmp_path, state, web, topics=SPACE_TOPICS)
    b = w.host.plan(SPACE_GOAL, None)
    assert b["knowledge_plan"]["topics"] == SPACE_TOPICS
    run = _yes_and_build(w, b)["knowledge_run"]
    titles = [v["title"] for v in _kept(state, "wikipedia")]
    assert sorted(titles) == sorted(AFTER), sorted(set(titles) ^ set(AFTER))
    assert not LEFT_OUT & set(titles)
    # The next page each search found was read in place of one left out ("sky clarity" kept 3, not 1).
    assert {"Astronomical seeing", "Light pollution"} <= set(titles)
    assert run["off_goal"]["wikipedia"] == len(LEFT_OUT) == 12
    assert "Left out 12 Wikipedia articles that were not about your goal." in run["line"]
    # Gutenberg: her build read 2 of 10 books; one word of "space telescope" finds a third, and a book that only
    # shares a word ("The Celestial Railroad") is left out.
    books = {v["title"] for v in _kept(state, "gutenberg")}
    assert books == {"Practical Talks by an Astronomer", "On the Construction of a Silvered Glass Telescope",
                     "The Telescope"}
    assert run["counts"] == {"wikipedia": len(AFTER), "gutenberg": 3}
    assert "Left out 1 Gutenberg book that was not about your goal." in run["line"]
    # Only the topics and their own words went out, never a word of the goal that was not on the plan card.
    allowed = set(SPACE_TOPICS) | {w for t in SPACE_TOPICS for w in t.split()}
    sent = _sent_words(web)
    assert set(sent) <= allowed and set(SPACE_TOPICS) <= set(sent)
    assert not {"bot", "outside", "pictures", "learn"} & {w for s in sent for w in s.split()}


def test_gutenberg_tries_a_topics_own_words_only_when_the_phrase_finds_nothing(tmp_path, state):
    web = SpaceWeb()
    w = _fake_world(tmp_path, state, web, topics=SPACE_TOPICS)
    run = _yes_and_build(w, w.host.plan(SPACE_GOAL, None))["knowledge_run"]
    asked = [v for u, _h in web.calls if "search.opds" in u
             for v in urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)["query"]]
    # The whole phrase first, then at most 2 of its words, longest first; a phrase that found books: no retry.
    assert asked.index("celestial events") < asked.index("celestial") < asked.index("events")
    assert asked.count("astronomy photography") == 1 and "astronomy" not in asked and "photography" not in asked
    assert len(asked) <= len(SPACE_TOPICS) * 3 and len(asked) == len(set(asked))
    assert "Gutenberg had nothing on celestial events, sky clarity or iss orbit." in run["line"]


def test_a_page_left_out_is_never_read_past_the_cap(tmp_path, state):
    """Every page is off the goal: the build stops replacing them at the source's own cap, and says so."""
    goal = "a chess bot: chess openings, chess endgames, chess tactics, chess clocks and chess history"
    topics = [f"topic {chr(97 + i)}" for i in range(5)]
    web = Web()
    w = _fake_world(tmp_path, state, web, topics=topics)
    run = _yes_and_build(w, w.host.plan(goal, None))["knowledge_run"]
    wiki = [c for c, _h in web.calls if "prop=extracts" in c]
    books = [c for c, _h in web.calls if "/cache/epub/" in c]
    assert run["counts"] == {"wikipedia": 0, "gutenberg": 0}
    assert len(wiki) <= 2 * 40 and len(books) <= 2 * 10
    assert run["line"].startswith("No knowledge was read into your bot.")
    assert "Wikipedia articles that were not about your goal." in run["line"]


# -- 2. the same check on unrelated goals; it names no field --------------------------------------------------
GOALS = {
    "garden": ("help me grow vegetables in my backyard garden and keep pests away",
               ["vegetable gardening", "garden pests", "garden soil", "companion planting"],
               ("companion planting", "Companion planting: growing different crops near each other in a vegetable "
                "garden for pest control and pollination."),
               ("companion planting", "In the television series Doctor Who, a companion is a character who travels "
                "with the Doctor; the planting of a flag is a famous scene.")),
    "homework": ("help my kids with their math and science homework after school",
                 ["math homework help", "fractions for kids", "science homework", "multiplication tables"],
                 ("multiplication tables", "A multiplication table lists products of numbers; kids learn it in math "
                  "lessons and homework."),
                 ("multiplication tables", "Tables is an American indie rock band; its second album was called "
                  "Multiplication.")),
    "book club": ("run our monthly book club: pick novels, plan each discussion and keep reading notes",
                  ["book club discussion", "literary fiction", "novel reading"],
                  ("literary fiction", "Literary fiction is fiction valued for style and depth; many a book club "
                   "picks such novels for discussion."),
                  ("literary fiction", "Fiction is a Swedish metal band from Gothenburg, known for literary lyrics "
                   "and long tours.")),
    "car care": ("remind me about car maintenance like oil changes and tire rotation",
                 ["car maintenance", "engine oil change", "tire rotation"],
                 ("tire rotation", "Tire rotation moves the wheels of a car from one place to another so the tires "
                  "wear evenly; it is part of car maintenance."),
                 ("tire rotation", "In mathematics, a rotation is a motion of a rigid body about a fixed point; a "
                  "rotation keeps distances.")),
    "space": (SPACE_GOAL, SPACE_TOPICS,
              ("sky clarity", "Astronomical seeing is how much the air blurs celestial objects in a telescope."),
              ("sky clarity", "Clarity is the debut studio album by Zedd; its singles include Fall into the Sky.")),
}


@pytest.mark.parametrize("name", sorted(GOALS))
def test_any_goal_keeps_its_own_pages_and_leaves_out_a_same_word_page(name):
    goal, topics, (t_on, on), (t_off, off) = GOALS[name]
    need = kmod.fit_words(goal, topics)
    assert kmod.fits_goal(on, need[t_on]), (name, sorted(need[t_on]))
    assert not kmod.fits_goal(off, need[t_off]), (name, sorted(kmod.subject_words(off) & need[t_off]))


def test_a_topic_word_the_goal_shares_counts_but_a_word_only_the_topic_has_does_not():
    need = kmod.fit_words("a chess bot", ["chess openings", "chess endgames"])
    assert kmod.fits_goal("The Sicilian Defence is a chess opening.", need["chess openings"])
    assert not kmod.fits_goal("Openings is a 2019 film about a theatre's first nights.", need["chess openings"])
    assert kmod.fit_words("", []) == {} and kmod.fits_goal("anything at all", frozenset())


def test_the_common_words_name_no_subject():
    domain = {"space", "astronomy", "sky", "garden", "plants", "pest", "wedding", "music", "homework", "math",
              "book", "novel", "car", "tire", "oil", "recipe", "dog", "chess", "photo", "picture", "event"}
    assert not domain & kmod._COMMON


# -- 3. public text the Core fetched is not her data; her own files keep the full screen ----------------------
ARTICLE = ("Marisa Abela\n\nFrom Wikipedia. Text from Wikipedia, by its authors, under CC BY-SA 4.0.\n\n"
           "Marisa Gabrielle Abela (born 7 December 1996) is a British actress. The observatory is at "
           "1340 Woodstock Road, Pasadena.\n")
META = {"url": "https://en.wikipedia.org/wiki/Marisa_Abela", "revision": 1, "license": "CC BY-SA 4.0",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "credit": "Text from Wikipedia.",
        "title": "Marisa Abela", "topic": "sky clarity", "fetched_at": "2026-10-27T21:10:00+00:00"}


def _row(state, path):
    with closing(sqlite3.connect(state / "index" / "knowledge.db")) as db:
        return db.execute("SELECT status, reason, held, top_tier FROM files WHERE path = ?",
                          (str(path.resolve()),)).fetchone()


def _sealed(state):
    log = state / "logs" / "vault.log"
    return log.read_text(encoding="utf-8").count('"inbox_sealed"') if log.is_file() else 0


def test_text_the_core_fetched_is_indexed_whole_and_never_sealed_into_her_inbox(state):
    a, *_ = make(state, open_=False)                     # locked: a held value would be sealed into the inbox
    path = fetched.save(state, "wikipedia", "marisa-abela-66628868", ARTICLE, dict(META, source="wikipedia"))
    before = (a.inbox.pending(), _sealed(state))
    got = Library(a).scan_file(path)
    assert got == {"status": "indexed", "reason": PUBLIC_REASON, "held": 0}
    assert _row(state, path) == ("indexed", PUBLIC_REASON, 0, "P0")
    assert (a.inbox.pending(), _sealed(state)) == before == (0, 0)
    assert Library(a).search("1996") and Library(a).search("Woodstock")        # indexed whole
    # The record is the Core's own, outside her data folder.
    ledger = state / "index" / fetched.LEDGER
    assert ledger.is_file() and fetched.data_folder(state) not in ledger.parents


def test_her_own_file_with_the_same_words_is_still_screened(state):
    a, *_ = make(state, open_=False)
    lib = Library(a)
    lib.data_folder.mkdir(parents=True, exist_ok=True)
    mine = lib.data_folder / "notes.txt"
    mine.write_text(ARTICLE, encoding="utf-8")
    got = lib.scan_file(mine)
    assert got["status"] == "indexed" and got["held"] >= 2 and got["reason"] == ""
    assert a.inbox.pending() >= 2 and _sealed(state) >= 2
    assert lib.search("1996") == []


def test_a_file_she_puts_in_the_knowledge_folder_with_a_meta_row_is_still_screened(state):
    """Where a file came from is the Core's own fetch record, never its folder, its name or meta.json."""
    a, *_ = make(state, open_=False)
    folder = fetched.root(state) / "wikipedia"
    folder.mkdir(parents=True)
    mine = folder / "my-notes-1.txt"
    mine.write_text(ARTICLE, encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"items": {"my-notes-1": dict(META, source="wikipedia",
                                                                                file=mine.name)}}),
                                      encoding="utf-8")
    got = Library(a).scan_file(mine)
    assert got["held"] >= 2 and got["reason"] == "" and a.inbox.pending() >= 2


def test_same_bytes_under_another_name_or_in_a_linked_folder_are_screened(state, tmp_path):
    a, *_ = make(state, open_=False)
    path = fetched.save(state, "gutenberg", "pg1-a-book", ARTICLE, dict(META, source="gutenberg"))
    twin = path.with_name("pg2-a-book.txt")
    shutil.copyfile(path, twin)
    mine = tmp_path / "Documents"
    mine.mkdir()
    shutil.copyfile(path, mine / "pg1-a-book.txt")
    lib = Library(a)
    lib.link(mine)
    assert lib.scan_file(path)["held"] == 0
    assert lib.scan_file(twin)["held"] >= 2
    assert lib.scan_file(mine / "pg1-a-book.txt")["held"] >= 2


def test_a_fetched_file_she_changes_gets_the_full_screen_again(state):
    a, *_ = make(state, open_=False)
    path = fetched.save(state, "wikipedia", "observatory-58968", "Observatory\n\nA place to watch the sky.\n" * 20,
                        dict(META, source="wikipedia"))
    lib = Library(a)
    assert lib.scan_file(path) == {"status": "indexed", "reason": PUBLIC_REASON, "held": 0}
    with path.open("a", encoding="utf-8") as f:
        f.write("My own note: I was born 7 December 1996 and live at 1340 Woodstock Road, Pasadena.\n")
    got = lib.scan_file(path)
    assert got["held"] >= 2 and got["reason"] == "" and a.inbox.pending() >= 2
    assert lib.search("Woodstock") == []


def test_a_record_she_writes_in_her_data_folder_counts_for_nothing(state):
    a, *_ = make(state, open_=False)
    folder = fetched.root(state) / "wikipedia"
    folder.mkdir(parents=True)
    mine = folder / "my-notes-2.txt"
    mine.write_bytes(ARTICLE.encode("utf-8"))
    import hashlib
    forged = {"files": {str(mine.resolve()): {"source": "wikipedia", "size": len(ARTICLE.encode()),
                                              "sha256": hashlib.sha256(ARTICLE.encode()).hexdigest()}}}
    for where in (fetched.data_folder(state) / fetched.LEDGER, fetched.root(state) / fetched.LEDGER,
                  folder / fetched.LEDGER):
        where.write_text(json.dumps(forged), encoding="utf-8")
    assert not fetched.written_by_fetch(state, mine, mine.read_bytes())
    assert Library(a).scan_file(mine)["held"] >= 2


def test_end_to_end_a_fetched_article_with_a_birth_date_and_an_address_is_not_held(real_core, state):
    app, core, host, ports, web = real_core
    from test_i11_knowledge import GOAL
    web.texts["Wedding Planning 0"] = ("A wedding planner, born 7 December 1996, plans each wedding from an office "
                                       "at 1340 Woodstock Road, Pasadena. " * 6)
    app.extensions["puffpai.zone3"].bridge.open()
    b = host.plan(GOAL, [{"name": "Vendor list", "does": "Keeps wedding vendors"}])
    cid = b["consent"]["id"]
    core.consent.answer(cid, "yes", "tap", by="owner", card_sha=core.consent.get(cid)["card_sha"])
    run = host.start(b["id"], cid)["knowledge_run"]
    assert run["counts"]["wikipedia"] >= 1 and all(i["indexed"] for i in run["items"])
    with closing(sqlite3.connect(state / "index" / "knowledge.db")) as db:
        rows = db.execute("SELECT path, held, reason FROM files").fetchall()
    knowledge = [r for r in rows if "knowledge" in r[0].replace("\\", "/").split("/")]
    assert knowledge and all(r[1] == 0 and r[2] == PUBLIC_REASON for r in knowledge)
    assert _sealed(state) == 0
    from puffpai_core.wiring import knowledge_search
    assert knowledge_search(core)("Woodstock", 5)
