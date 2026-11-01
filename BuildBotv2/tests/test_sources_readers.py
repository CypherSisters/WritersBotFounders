"""D99: the small readers for the added Toolsmith sources, on answers shaped like what each service sent on 2026-10-30.

No network: every answer here is a short recorded shape. Three unrelated goals, since every source is goal-agnostic.
"""

from __future__ import annotations

import json
import re

import pytest

from puffpai_core.zone1 import fetched
from puffpai_core.zone3.policy import KNOWLEDGE_HOSTS
from puffpai_core.zone3.toolsmith import find, shelves
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from puffpai_core.zone3.toolsmith_host import knowledge_downloads as dl
from puffpai_core.zone3.toolsmith_host import knowledge_readers as readers


def j(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


SRC = find.sources()
ROWS = {**SRC["shelves"], **SRC["web"]}


def test_every_row_names_a_reader_this_code_has_and_carries_its_host():
    for rid, row in ROWS.items():
        assert row["reader"] in shelves.READERS, rid
        assert row["tries"] in ("git", "github_link", "look"), rid
        assert row["host"], rid
    for sid, row in SRC["fetch"].items():
        assert row["reader"] in kmod.READERS, sid
        assert sid in fetched.SOURCES and row["host"] in KNOWLEDGE_HOSTS, sid
        assert row["license"] and row["license_url"] and row["credit"], sid
    for sid, row in SRC["catalogs"].items():
        assert row["reader"] in kmod.CATALOGS, sid
        assert row["license"] and row["credit"], sid


def test_the_dropped_sources_stay_dropped():
    text = json.dumps({k: SRC[k] for k in ("shelves", "web", "fetch", "catalogs")})
    assert "stackexchange" not in text and "openverse" not in text


def test_no_row_carries_a_goals_own_words():
    """Only source-shaped gates: nothing about space, weddings or recipes."""
    for row in [*ROWS.values(), *SRC["fetch"].values(), *SRC["catalogs"].values()]:
        for w in row.get("only_when") or []:
            assert w not in ("astronomy", "space", "wedding", "recipe", "chess")


def test_codeberg_keeps_projects_and_leaves_out_mirrors_and_private():
    rows = shelves.read_forgejo("codeberg", ROWS["codeberg"], j({"ok": True, "data": [
        {"full_name": "alice/recipes", "name": "recipes", "description": "A recipe box", "stars_count": 9,
         "language": "Python", "default_branch": "main", "email": "x@y.z"},
        {"full_name": "bob/copy", "name": "copy", "mirror": True},
        {"full_name": "bob/secret", "name": "secret", "private": True},
        {"full_name": "../evil", "name": "evil"}]}))
    assert [r["full_name"] for r in rows] == ["alice/recipes"]
    assert rows[0]["host"] == "codeberg.org" and rows[0]["default_branch"] == "main" and "x@y.z" not in json.dumps(rows)


def test_npm_tries_the_linked_github_project_and_never_keeps_who_published():
    rows = shelves.read_npm("npm", ROWS["npm"], j({"objects": [
        {"package": {"name": "invoice-app", "description": "Invoices", "keywords": ["invoice"],
                     "publisher": {"email": "person@example.com"}, "maintainers": [{"email": "m@example.com"}],
                     "links": {"repository": "git+https://github.com/acme/invoice-app.git"}}},
        {"package": {"name": "no-link", "description": "x", "links": {}}}]}))
    assert rows[0]["full_name"] == "acme/invoice-app" and rows[0]["from"] == "npm"
    assert rows[1]["look"] is True and rows[1]["url"].endswith("/no-link")
    assert "@example.com" not in json.dumps(rows)


def test_pypi_exact_name_gives_its_github_project_or_something_to_look_at():
    linked = shelves.read_pypi("pypi", ROWS["pypi"], j({"info": {"name": "bird-log", "summary": "A bird log",
                                                               "keywords": "birds, log",
                                                               "project_urls": {"Source": "https://github.com/acme/bird-log"}}}))
    assert linked[0]["full_name"] == "acme/bird-log" and "birds" in linked[0]["topics"]
    alone = shelves.read_pypi("pypi", ROWS["pypi"], j({"info": {"name": "alone", "summary": "s", "project_urls": None}}))
    assert alone[0]["look"] is True
    assert shelves.word_for(ROWS["pypi"], ["bird", "log"]) == "bird-log"
    assert shelves.search_url(ROWS["pypi"], "bird-log") == "https://pypi.org/pypi/bird-log/json"


@pytest.mark.parametrize("rid", ["mcp_registry", "hacs", "clawhub", "skills_sh", "hermes_index", "skill_collections"])
def test_look_only_shelves_are_marked_look(rid):
    assert ROWS[rid]["tries"] == "look"


def test_mcp_clawhub_skills_hacs_hermes_and_tree_give_rows_to_look_at():
    mcp = shelves.read_mcp("mcp_registry", ROWS["mcp_registry"], j({"servers": [
        {"server": {"name": "a/b", "title": "Plant guide", "description": "Plants", "websiteUrl": "https://plants.example/x"}},
        {"server": {"name": "c/d", "description": "no address"}}]}))
    assert [m["name"] for m in mcp] == ["Plant guide"]
    claw = shelves.read_clawhub("clawhub", ROWS["clawhub"], j({"results": [
        {"displayName": "Garden", "canonicalUrl": "/owner/skills/garden",
         "native": {"skill": {"summary": "Track a garden", "isSuspicious": False}}},
        {"displayName": "Bad", "canonicalUrl": "/owner/skills/bad", "native": {"skill": {"isSuspicious": True}}}]}))
    assert [c["name"] for c in claw] == ["Garden"] and claw[0]["url"] == "https://clawhub.ai/owner/skills/garden"
    sh = shelves.read_skills_sh("skills_sh", ROWS["skills_sh"], j({"skills": [
        {"id": "a/b/garden-wiki", "source": "a/b", "name": "Garden wiki"}, {"id": "../x", "name": "x"}]}))
    assert [s["url"] for s in sh] == ["https://skills.sh/a/b/garden-wiki"]
    hacs = shelves.read_hacs("hacs", ROWS["hacs"], j({"1": {"full_name": "a/blinds", "manifest_name": "Blinds", "description": "d",
                                                         "topics": ["blinds"]}}))
    assert hacs[0]["url"] == "https://github.com/a/blinds"
    hermes = shelves.read_hermes_index("hermes_index", ROWS["hermes_index"], shelves.slim_hermes(j({"skills": [
        {"name": "1password", "description": "Secrets", "repo": "acme/hermes", "path": "skills/1password", "extra": {"big": "x" * 500}},
        {"name": "no-repo", "description": "d"}]})))
    assert [h["name"] for h in hermes] == ["1password"] and "tree/HEAD/skills/1password" in hermes[0]["url"]
    tree = shelves.read_gh_tree("skill_collections", dict(ROWS["skill_collections"], _url=ROWS["skill_collections"]["list"][0]),
                                j({"tree": [{"path": "skills/pdf-tools/SKILL.md"}, {"path": "README.md"}]}))
    assert tree[0]["name"] == "pdf tools" and tree[0]["url"].startswith("https://github.com/anthropics/skills/tree/HEAD/")


def test_a_web_result_that_is_a_project_is_tried_and_brave_results_are_not_kept():
    body = j({"web": {"results": [{"title": "App", "url": "https://github.com/a/app", "description": "d"},
                                  {"title": "A <b>page</b>", "url": "https://example.com/page", "description": "d"}]}})
    assert [r["full_name"] for r in shelves.read_brave("brave", ROWS["brave"], body)] == ["a/app"]
    rows = shelves.read_marginalia("marginalia", ROWS["marginalia"], j({"results": [
        {"url": "https://github.com/a/app", "title": "App", "description": ""},
        {"url": "https://example.com/page", "title": "A page", "description": "d"}]}))
    assert rows[0]["full_name"] == "a/app" and rows[0]["listed"] is True and rows[1]["look"] is True


def test_usable_only_asks_places_her_search_yes_names_and_web_only_with_a_key():
    rows, missing = shelves.usable(SRC, {"npm.example"}, set(), "a recipe bot")
    assert not rows and "codeberg" in missing
    rows, missing = shelves.usable(SRC, {"codeberg.org", "api.search.brave.com"}, set(), "a recipe bot")
    assert list(rows) == ["codeberg"]
    rows, _ = shelves.usable(SRC, {"api.search.brave.com"}, {"brave"}, "a recipe bot")
    assert list(rows) == ["brave"]


@pytest.mark.parametrize("goal", ["an invoices bot for a small shop", "a recipe box", "control my Home Assistant lights"])
def test_every_place_is_asked_for_every_goal(goal):
    """The owner, 10-30: "We should be searching ALL of the databases, not just a couple." """
    every = {row["host"] for row in SRC["shelves"].values()}
    rows, missing = shelves.usable(SRC, every, set(), goal)
    assert set(rows) == set(SRC["shelves"]) and not missing
    fetched_rows = {f["source"] for f in kmod.plan_of(["plain topic"], context=goal)["fetch"]}
    assert fetched_rows >= {"wikipedia", "gutenberg", "appropedia", "ifixit"}


def test_no_row_is_left_out_by_a_budget():
    assert not any("per_plan" in r for r in SRC["shelves"].values())
    assert all(int(r.get("per_job") or 0) >= 1 for r in SRC["shelves"].values() if r.get("search"))


class Slow:
    def __init__(self, delay=0.0, boom=None):
        self.delay, self.boom, self.calls = delay, boom, []

    def http_get(self, url, **kw):
        import time
        self.calls.append(url)
        if self.boom:
            raise self.boom
        time.sleep(self.delay)
        return 200, {}, j({"objects": []})


def test_a_failing_or_late_place_is_out_for_the_plan_and_the_others_answer(tmp_path):
    class Mixed(Slow):
        def http_get(self, url, **kw):
            if "codeberg" in url:
                import time
                time.sleep(1.5)
            if "pypi" in url:
                raise OSError("boom")
            return 200, {}, j({"objects": [{"package": {"name": "x", "links": {"repository": "https://github.com/a/x"}}}]})
    r = shelves.Round(Mixed(), tmp_path, SRC)
    asks = [("codeberg", ROWS["codeberg"], "https://codeberg.org/q"), ("pypi", ROWS["pypi"], "https://pypi.org/pypi/x/json"),
            ("npm", ROWS["npm"], "https://registry.npmjs.org/q")]
    got = r.run(asks, wait_s=0.3)
    assert [g["full_name"] for g in got] == ["a/x"]
    assert set(r.down) == {"codeberg", "pypi"}
    assert shelves.down_line(["Codeberg", "PyPI"]) == "No answer from Codeberg and PyPI, so nothing from them is on this plan."


def test_a_place_that_fails_is_not_asked_again_for_the_next_jobs(tmp_path):
    link = Slow(boom=OSError("no"))
    r = shelves.Round(link, tmp_path, SRC)
    row = dict(ROWS["npm"], gap_s=0, cache_hours=0)
    r.begin([("npm", row, "https://registry.npmjs.org/a", 0), ("npm", row, "https://registry.npmjs.org/b", 1)])
    r.wait([0, 1], 5)
    assert link.calls == ["https://registry.npmjs.org/a"] and "npm" in r.down
    assert not r._waiting[1]                                 # no job waits on a place that is out


def test_finish_says_a_late_place_gave_no_answer(tmp_path):
    r = shelves.Round(Slow(delay=2), tmp_path, SRC)
    r.begin([("npm", dict(ROWS["npm"], cache_hours=0), "https://registry.npmjs.org/a", 0)])
    r.finish()
    assert r.down == {"npm": "it did not answer in time"} and r.asked == ["npm"]


def test_a_place_is_never_asked_twice_at_once(tmp_path):
    link = Slow()
    r = shelves.Round(link, tmp_path, SRC)
    row = dict(ROWS["npm"], gap_s=0.01, cache_hours=0)
    r.run([("npm", row, "https://registry.npmjs.org/a"), ("npm", row, "https://registry.npmjs.org/b")], wait_s=5)
    assert link.calls == ["https://registry.npmjs.org/a", "https://registry.npmjs.org/b"]
    assert not r.down


def test_brave_answers_are_never_cached(tmp_path):
    assert ROWS["brave"]["cache_hours"] == 0 and ROWS["marginalia"]["cache_hours"] == 0


# -- knowledge ---------------------------------------------------------------------------------------------------

KIWIX = b"""<feed><entry><title>Gardenology</title><summary>A complete plant and garden encyclopedia</summary>
<name>gardenology_en_all</name><flavour></flavour><category></category><articleCount>44083</articleCount>
<link rel="http://opds-spec.org/acquisition/open-access" type="application/x-zim"
 href="https://lb.download.kiwix.org/zim/other/gardenology.org_en_all_2026-09.zim.meta4" length="112499712" /></entry>
<entry><title>TED gardening</title><name>ted_mul_gardening</name><category>ted</category><flavour></flavour>
<link rel="http://opds-spec.org/acquisition/open-access" href="https://lb.download.kiwix.org/zim/ted/x.zim.meta4" length="9" /></entry>
<entry><title>Big gardens</title><summary>All gardens</summary><name>big_gardens</name><flavour>maxi</flavour><category></category>
<link rel="http://opds-spec.org/acquisition/open-access" href="https://lb.download.kiwix.org/zim/o/big.zim.meta4" length="5000000000" /></entry>
<entry><title>Elsewhere</title><name>elsewhere</name><flavour></flavour><category></category>
<link rel="http://opds-spec.org/acquisition/open-access" href="https://evil.example/zim/x.zim.meta4" length="5" /></entry>
</feed>"""


def test_kiwix_reads_books_and_refuses_a_far_host_and_video_books():
    rows = readers.kiwix_found(KIWIX, "lb.download.kiwix.org")
    assert [r["id"] for r in rows] == ["gardenology_en_all", "big_gardens"]
    assert rows[0]["bytes"] == 112499712 and rows[0]["articles"] == 44083


def test_meta4_needs_its_hash_and_size():
    good = readers.meta4_of(b'''<metalink><file name="a.zim"><size>100</size><hash type="md5">x</hash>
<hash type="sha-256">''' + b"a" * 64 + b'''</hash><url priority="1">https://mirror.download.kiwix.org/zim/a.zim</url></file></metalink>''')
    assert good["size"] == 100 and good["urls"] == ["https://mirror.download.kiwix.org/zim/a.zim"]
    assert readers.meta4_of(b'<file name="a.zim"><size>100</size></file>') is None


DATAGOV = j({"results": [
    {"slug": "city-water", "publisher": "USGS", "dcat": {"accessLevel": "public", "title": "City garden water", "description": "Water for gardens",
     "contactPoint": {"hasEmail": "mailto:someone@usgs.gov"},
     "distribution": [{"downloadURL": "https://data.usgs.gov/m.xml", "format": "XML", "title": "Original Metadata"},
                      {"downloadURL": "https://data.usgs.gov/d.csv", "format": "CSV", "title": "Data"}]}},
    {"slug": "far-away", "dcat": {"accessLevel": "public", "title": "Far", "distribution": [{"downloadURL": "https://evil.example/d.csv", "format": "CSV"}]}},
    {"slug": "plain-http", "dcat": {"accessLevel": "public", "title": "Plain", "distribution": [{"downloadURL": "http://data.usgs.gov/d.csv", "format": "CSV"}]}},
    {"slug": "landing", "dcat": {"accessLevel": "public", "title": "Landing", "distribution": [{"accessURL": "https://doi.org/x", "format": "XML"}]}},
    {"slug": "closed", "dcat": {"accessLevel": "restricted public", "title": "Closed", "distribution": [{"downloadURL": "https://data.usgs.gov/c.csv", "format": "CSV"}]}}]})


def test_datagov_keeps_only_a_real_file_on_a_gov_host_and_no_contact_email():
    rows = readers.datagov_found(DATAGOV, SRC["catalogs"]["datagov"])
    assert [r["id"] for r in rows] == ["city-water"]
    assert rows[0]["file"] == "https://data.usgs.gov/d.csv" and rows[0]["format"] == "csv"
    assert "usgs.gov\"" not in json.dumps(rows) and "someone" not in json.dumps(rows)


def test_hugging_face_is_look_only_and_leaves_out_gated_and_unknown_licenses():
    rows = readers.hf_found(j([
        {"id": "a/garden", "private": False, "gated": False, "tags": ["license:cc-by-4.0"], "cardData": {"pretty_name": "Garden data"}},
        {"id": "b/closed", "private": False, "gated": "auto", "tags": ["license:mit"]},
        {"id": "c/unknown", "private": False, "tags": ["license:unknown"]},
        {"id": "d/none", "private": False, "tags": []}]), SRC["catalogs"]["huggingface"])
    assert [r["id"] for r in rows] == ["a/garden"] and rows[0]["license"] == "CC-BY-4.0"
    assert SRC["catalogs"]["huggingface"]["look_only"] is True


def test_ifixit_reads_guides_and_their_steps():
    found = readers.ifixit_found(j({"results": [
        {"dataType": "guide", "guideid": 7, "url": "https://www.ifixit.com/Guide/Fix/7", "title": "Fix a hose"},
        {"dataType": "wiki", "guideid": 8, "url": "https://www.ifixit.com/x", "title": "Not a guide"}]}))
    assert found[0][0] == 7 and found[0][2]["url"].endswith("/7")
    text = readers.ifixit_guide(j({"introduction_rendered": "<p>Start.</p>", "steps": [
        {"title": "Remove", "lines": [{"text_raw": "Pull it off."}]}, {"title": "", "lines": []}]}))
    assert "Start." in text and "Step 1: Remove\nPull it off." in text


def test_commons_keeps_a_plain_picture_with_a_license_and_its_author():
    hosts = SRC["fetch"]["commons"]["file_hosts"]
    def page(pid, title, mime="image/jpeg", licence="CC BY 4.0", thumb="https://upload.wikimedia.org/a.jpg"):
        return {"pageid": pid, "title": title, "index": pid, "imageinfo": [{
            "mime": mime, "thumburl": thumb, "descriptionurl": f"https://commons.wikimedia.org/wiki/{pid}",
            "extmetadata": {"LicenseShortName": {"value": licence}, "Artist": {"value": "<a href='x'>Ann</a>"}}}]}
    rows = readers.commons_found(j({"query": {"pages": [
        page(1, "File:Rose.jpg"), page(2, "File:Vector.svg", mime="image/svg+xml"), page(3, "File:None.jpg", licence=""),
        page(4, "File:Far.jpg", thumb="https://evil.example/a.jpg")]}}), hosts)
    assert [r[0] for r in rows] == [1] and rows[0][1] == "Rose"
    assert rows[0][2]["who"] == "Ann" and rows[0][2]["license"] == "CC BY 4.0"


@pytest.mark.parametrize("head,kind", [(b"\xff\xd8\xff\xe0abc", "jpg"), (b"\x89PNG\r\n\x1a\nxx", "png"), (b"GIF89a..", "gif"),
                                       (b"RIFF1234WEBPxxxx", "webp"), (b"<svg onload=x>", ""), (b"<html>", "")])
def test_a_picture_is_known_by_its_own_first_bytes(head, kind):
    assert readers.picture_kind(head) == kind


GOALS = ["a recipe box for my family's dinners", "invoices for a small flower shop", "a birdwatching log for my garden"]


@pytest.mark.parametrize("goal", GOALS)
def test_a_plain_plan_with_no_finds_reads_every_article_source(goal):
    kp = kmod.plan_of(["plain topic"], context="", goal=goal)
    assert [f["source"] for f in kp["fetch"]] == ["wikipedia", "gutenberg", "appropedia", "ifixit"]
    assert kp["hosts"] == ["en.wikipedia.org", "www.appropedia.org", "www.gutenberg.org", "www.ifixit.com"]
    assert {o["source"] for o in kp["offered"]} == {"kiwix", "datagov"}


def test_pictures_fit_a_job_that_wants_pictures():
    def sources(context):
        return [f["source"] for f in kmod.plan_of(["plain topic"], context=context)["fetch"]]
    assert "commons" in sources("a poster and picture wall") and "commons" not in sources("invoices")


def offers_for(finds, topics=("garden plants",), goal="a garden plants helper"):
    return kmod.best_offers(finds, list(topics), goal)


def row(i, size, title="Garden plants book"):
    return {"id": f"book{i}", "title": title, "summary": "About garden plants", "meta": "https://lb.download.kiwix.org/z/a.zim.meta4", "bytes": size}


def test_best_five_at_most_over_two_gb_starts_unticked_and_each_carries_its_license():
    finds = {"kiwix": [row(i, 1_000_000 * (i + 1)) for i in range(3)] + [row(9, 3_000_000_000)],
             "datagov": [{"id": f"d{i}", "title": "Garden plants data", "summary": "garden plants", "file": "https://data.usda.gov/d.csv", "format": "csv"} for i in range(4)]}
    got = offers_for(finds)
    assert len(got) == 5
    big = kmod.best_offers({"kiwix": [row(9, 3_000_000_000)]}, ["garden plants"], "a garden plants helper")[0]
    assert big["ticked"] is False and "starts unticked" in big["line"]
    assert all(o["license"] and "License:" in o["line"] for o in got)
    assert all(o["ticked"] for o in got if o["bytes"] <= 2_000_000_000)


def test_a_find_that_is_not_about_the_goal_is_left_out():
    far = {"id": "x", "title": "Tax records", "summary": "Tax", "meta": "https://lb.download.kiwix.org/z.meta4", "bytes": 5}
    assert kmod.best_offers({"kiwix": [far]}, ["garden plants"], "a garden plants helper") == []


def test_a_data_file_off_a_gov_host_is_never_offered():
    far = {"id": "x", "title": "Garden plants", "summary": "garden plants", "file": "https://data.evil.example/x.csv", "format": "csv"}
    assert kmod.best_offers({"datagov": [far]}, ["garden plants"], "a garden plants helper") == []


def test_the_card_names_every_place_the_topics_go_after_the_yes():
    kp = kmod.plan_of(["garden hose"], context="repair the garden hose")
    line = kmod.sent_line(kp)
    for name in ("Wikipedia", "Gutenberg", "Appropedia", "iFixit"):
        assert name in line
    joined = " ".join(kmod.card_lines(kp))
    assert "www.ifixit.com" in joined and "personal use only" in joined and "www.appropedia.org" in joined


def test_ticks_decide_which_downloads_start():
    kp = kmod.plan_of(["garden plants"], finds={"kiwix": [row(1, 5), row(2, 3_000_000_000)]}, goal="a garden plants helper")
    values = [o["value"] for o in kp["offers"]]
    default = [u["value"] for u in kmod.start_run(kp)["queue"] if u["do"] == "download"]
    assert default == [values[0]]
    assert [u["value"] for u in kmod.start_run(kp, [values[1]])["queue"] if u["do"] == "download"] == [values[1]]
    assert [u for u in kmod.start_run(kp, [])["queue"] if u["do"] == "download"] == []


def test_package_ids_fit_the_registry_and_the_folder_rule():
    for item in ("gardenology_en_all", "x" * 90, "a-b-c"):
        pid = dl.package_id("datagov", item)
        assert re.fullmatch(r"[a-z][a-z0-9-]{1,39}", pid) and fetched.PACKAGE.match(pid)
    assert dl.package_id("kiwix", "a" * 90) != dl.package_id("kiwix", "a" * 89)


def test_a_package_folder_is_only_ever_inside_knowledge(tmp_path):
    for bad in ("../evil", "kn-../x", "kn-", "other"):
        with pytest.raises(ValueError):
            fetched.package_folder(tmp_path, bad)
    with pytest.raises(ValueError):
        fetched.save_file(tmp_path, "commons", "../evil.jpg", b"x")
    with pytest.raises(ValueError):
        fetched.save_file(tmp_path, "commons", "a.svg", b"x")


def test_text_from_a_package_counts_as_fetched_only_with_its_exact_bytes(tmp_path):
    p = fetched.save(tmp_path, "kn-kiwix-abc-1234", "one", "hello", {"title": "t", "url": "https://x.example/a"})
    data = p.read_bytes()
    assert fetched.written_by_fetch(tmp_path, p, data)
    assert not fetched.written_by_fetch(tmp_path, p, data + b"x")
    assert fetched.sources(tmp_path)[0]["source"] == "kn-kiwix-abc-1234"
    mine = fetched.root(tmp_path) / "notmine" / "one.txt"
    mine.parent.mkdir()
    mine.write_bytes(data)
    assert not fetched.written_by_fetch(tmp_path, mine, data)


def test_a_range_read_keeps_going_and_refuses_a_restart(tmp_path):
    part = tmp_path / ".x.part"
    sent = []

    def send(url, headers, most, timeout):
        sent.append(headers["Range"])
        return 206, b"a" * 10
    assert dl.fetch_range(send, "https://x/a", part, 20) == 10
    assert dl.fetch_range(send, "https://x/a", part, 20) == 20
    assert sent[1].startswith("bytes=10-")
    with pytest.raises(dl.DownloadFailed):
        dl.fetch_range(lambda *a: (200, b"zzz"), "https://x/a", part, 30)
