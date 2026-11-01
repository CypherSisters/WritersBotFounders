"""D89 (lane store): the Core installs from the PuffPAI Store.

Every repo here is a local git repo in the test's own folder, reached through ``store_index.REPO_MIRRORS``;
the official source is a local folder (``OFFICIAL_SOURCE`` patched) and an https source's GET is patched
(``store_index.http_get``). Nothing here uses the network.
"""

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from conftest import HELLO, git_init, yes
from puffpai_core.packages import store_index
from puffpai_core.packages.manager import AddError, store_subfolder
from puffpai_core.packages.store_index import Store, StoreError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

REPO_URL = "https://github.com/CypherSisters/puffpai-packages"
HTTPS_SOURCE = "https://store.example.invalid/index.json"


def _git(repo, *args):
    got = subprocess.run(["git", "-C", str(repo), "-c", "user.name=Cypher Sisters",
                          "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", *args],
                         check=True, capture_output=True, text=True)
    return got.stdout.strip()


def _commit(repo, words):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", words)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def pkgrepo(tmp_path):
    """A packages repo with two folders: hello-tool (1.0.0 at c1, 1.1.0 with new.txt at c2) and other."""
    repo = tmp_path / "pkgrepo"
    repo.mkdir()
    git_init(repo)
    shutil.copytree(HELLO, repo / "hello-tool", ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    (repo / "other").mkdir()
    (repo / "other" / "note.txt").write_text("not this one", encoding="utf-8")
    (repo / "README.md").write_text("# packages\n", encoding="utf-8")
    c1 = _commit(repo, "one")
    m = json.loads((repo / "hello-tool" / "puffpai.json").read_text(encoding="utf-8"))
    m["version"] = "1.1.0"
    (repo / "hello-tool" / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    (repo / "hello-tool" / "new.txt").write_text("new", encoding="utf-8")
    c2 = _commit(repo, "two")
    return SimpleNamespace(path=repo, c1=c1, c2=c2)


def _row(commit, id="hello-tool", kind="tool", path="hello-tool", tier="verified", **more):
    return {"id": id, "kind": kind, "name": "Hello Tool", "description": "Says hello.", "version": "1.0.0",
            "author": "Cypher Sisters", "license": "MIT", "repo": REPO_URL, "path": path, "commit": commit,
            "tier": tier, **more}


def _index(folder, rows, yanked=()):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.json").write_text(json.dumps({"schema": "puffpai-store-index/1", "name": "Test store",
                                                   "packages": rows, "yanked": list(yanked)}), encoding="utf-8")
    return folder


@pytest.fixture
def env(core, monkeypatch, tmp_path, pkgrepo):
    """The official store is a local folder; the repo address goes to the local repo."""
    official = tmp_path / "official"
    _index(official, [])
    monkeypatch.setattr(store_index, "OFFICIAL_SOURCE", str(official))
    monkeypatch.setitem(store_index.REPO_MIRRORS, REPO_URL, str(pkgrepo.path))
    core.packages.start_after_install = False
    core.packages.store.fresh_s = 0
    return SimpleNamespace(core=core, pm=core.packages, store=core.packages.store, official=official,
                           repo=pkgrepo, tmp=tmp_path)


def _staging(core):
    return list((core.state / "packages" / ".staging").iterdir())


def _second(env, rows, yanked=()):
    folder = _index(env.tmp / "second", rows, yanked)
    env.store.add_source(str(folder))
    return folder


# -- 1. ACCEPTANCE: only the official source makes a package Verified --------------------------------------------

def test_official_verified_row_installs_as_store_verified(env, drawbridge):
    _index(env.official, [_row(env.repo.c1)])
    staged = env.pm.add("store:hello-tool")
    assert staged["card"]["origin"] == "store:verified"
    assert staged["card"]["store"]["tier"] == "verified" and staged["card"]["store"]["commit"] == env.repo.c1
    assert yes(env.core, staged)["status"] == "installed"
    row = env.core.registry.get("hello-tool")
    assert row["origin"] == "store:verified"
    assert row["store"] == {"source": "official", "repo": REPO_URL, "path": "hello-tool", "commit": env.repo.c1}


def test_a_second_source_saying_verified_installs_as_community(env, monkeypatch):
    seen = []
    from conftest import drawbridge_double
    drawbridge_double(monkeypatch, lambda p, m: seen.append(m["id"]) or {
        "verdict": "allow", "reasons": [], "rows": [], "line": "passed every check."})
    from puffpai_core import seams
    real = seams.drawbridge_check
    origins = []
    monkeypatch.setattr(seams, "drawbridge_check",
                        lambda folder, manifest, *, origin="local", drawbridge=None:
                        origins.append(origin) or real(folder, manifest, origin=origin, drawbridge=drawbridge))
    second = _second(env, [_row(env.repo.c1, tier="verified")])
    listed = env.store.listing()["packages"]
    assert [(r["id"], r["tier"], r["listed_tier"], r["source"]) for r in listed] == [
        ("hello-tool", "community", "verified", str(second))]
    staged = env.pm.add("store:hello-tool")
    assert staged["card"]["origin"] == "store:community" and origins == ["store:community"]
    assert staged["card"]["store"]["tier"] == "community"
    assert yes(env.core, staged)["status"] == "installed"
    row = env.core.registry.get("hello-tool")
    assert row["origin"] == "store:community" and row["store"]["source"] == str(second)
    from puffpai_core.steward.steward import community
    assert community(row["origin"]) is True                       # Steward's community rules apply


def _provider_repo(env, **extra):
    """hello-tool made a provider in the repo (kind provider), committed; returns the commit."""
    path = env.repo.path / "hello-tool" / "puffpai.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    m.update(kind="provider", provides=[{"slot": "zone2.embedder", "contract": 1}], reads="raw", **extra)
    m.pop("abilities", None)
    path.write_text(json.dumps(m), encoding="utf-8")
    return _commit(env.repo.path, "provider")


def test_drawbridge_community_rules_apply_to_a_second_sources_verified_row(env):
    c3 = _provider_repo(env)
    _second(env, [_row(c3, kind="provider", tier="verified")])
    staged = env.pm.add("store:hello-tool")                # the real Drawbridge: a community raw reader is held
    assert staged["card"]["origin"] == "store:community" and staged["card"]["drawbridge"]["verdict"] == "hold"
    env.core.consent.answer(staged["consent_id"], "no", by="test")
    assert env.pm.wait(staged["consent_id"], 10)["status"] == "declined"
    _index(env.official, [_row(c3, kind="provider", tier="verified")])
    staged = env.pm.add("store:hello-tool")                # the same files from the official store: not held as such
    assert staged["card"]["origin"] == "store:verified"
    from puffpai_core.zone3.drawbridge import Drawbridge
    folder = env.pm._pending[staged["consent_id"]].folder
    manifest = env.pm._pending[staged["consent_id"]].manifest
    assert "raw_reader" not in Drawbridge().check_package(folder, manifest, origin="store:verified")["reasons"]
    assert "raw_reader" in Drawbridge().check_package(folder, manifest, origin="store:community")["reasons"]


def test_a_community_provider_with_admin_never_installs(env, drawbridge):
    c3 = _provider_repo(env, needs={"admin": True, "hosts": []})
    _second(env, [_row(c3, kind="provider", tier="verified")])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert "Only Verified providers" in e.value.problems[0] and _staging(env.core) == []


def test_the_official_source_cant_be_removed_or_replaced(env, tmp_path):
    with pytest.raises(StoreError) as e:
        env.store.remove_source(str(env.official))
    assert e.value.status == 409
    with pytest.raises(StoreError) as e:
        env.store.add_source(str(env.official))
    assert e.value.status == 409
    # A hand-edited settings file naming the official address is not a second official source.
    (env.core.state / "settings").mkdir(parents=True, exist_ok=True)
    (env.core.state / "settings" / "store.json").write_text(
        json.dumps({"sources": [{"source": str(env.official)}, {"source": "http://plain.example/i.json"}]}),
        encoding="utf-8")
    assert [s["official"] for s in env.store.sources()] == [True]
    assert env.store.sources()[0]["source"] == str(env.official)


@pytest.mark.parametrize("bad", ["http://example.com/index.json", "ftp://example.com/i.json",
                                 "https://name:pw@example.com/index.json", "relative\\folder",
                                 "\\\\server\\share\\store", ""])
def test_bad_sources_are_refused(env, bad):
    with pytest.raises(StoreError):
        env.store.add_source(bad)
    assert len(env.store.sources()) == 1


# -- 2 and 3. The commit pin, and only the row's folder ---------------------------------------------------------

def test_the_pinned_older_commit_is_installed_and_only_its_folder(env, drawbridge):
    _index(env.official, [_row(env.repo.c1)])           # c2 is the branch head
    assert yes(env.core, env.pm.add("store:hello-tool"))["status"] == "installed"
    final = env.core.state / "packages" / "hello-tool"
    assert json.loads((final / "puffpai.json").read_text(encoding="utf-8"))["version"] == "1.0.0"
    assert not (final / "new.txt").exists()
    assert (final / "server.py").is_file() and (final / "icons" / "hello.svg").is_file()
    for absent in ("other", "TOP.md", ".git", "hello-tool"):          # the repo's other folders are not there
        assert not (final / absent).exists(), absent
    assert not any((env.core.state / "packages").glob("**/note.txt"))
    assert _staging(env.core) == []


# -- 4. Traversal, links, a missing or wrong commit ------------------------------------------------------------

@pytest.mark.parametrize("path", ["../hello-tool", "/hello-tool", "\\hello-tool", "C:/hello-tool", "hello-tool/..",
                                  "a\\b", "./hello-tool"])
def test_a_path_that_leaves_the_repo_is_dropped(env, path):
    _index(env.official, [_row(env.repo.c1, path=path)])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert e.value.status == 404 and "No store lists" in e.value.problems[0]
    assert env.store.index(env.store.sources()[0])["dropped"][0].startswith("hello-tool: path must be")
    assert _staging(env.core) == []


@pytest.mark.parametrize("field", ["id", "commit", "repo", "path"])
def test_a_value_ending_in_a_newline_is_refused(field):
    row = _row("a" * 40)
    row[field] = row[field] + "\n"
    assert store_index.row_problems(row), field
    index, dropped = store_index.parse_index({"schema": "puffpai-store-index/1", "packages": [row]})
    assert index["packages"] == [] and len(dropped) == 1


def test_a_yank_entry_ending_in_a_newline_is_left_out():
    index, dropped = store_index.parse_index({"schema": "puffpai-store-index/1", "packages": [], "yanked": [
        {"id": "hello-tool\n", "commit": "a" * 40, "why": "x"}, {"id": "hello-tool", "commit": "a" * 40 + "\n",
                                                                  "why": "x"}]})
    assert index["yanked"] == [] and len(dropped) == 2


@pytest.mark.parametrize("path", ["a\n", "hello-tool\n", "a/b\n", "hello-tool.", "a/b.", "hello-tool ", "a /b",
                                  "CON", "con", "nul.txt", "Aux", "prn/x", "x/COM1", "lpt9.json", "com3.tar.gz"])
def test_a_path_windows_reads_differently_is_refused(path):
    assert store_index.path_problem(path) is not None
    assert store_index.row_problems(_row("a" * 40, path=path))


@pytest.mark.parametrize("path", ["hello-tool", "a/b", "console", "nul-tool", "com10", "lpt0", "my.app",
                                  "packages/v1.2/app", ""])
def test_plain_paths_still_pass(path):
    assert store_index.path_problem(path) is None


def test_store_id_with_a_newline_is_refused(env):
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool\n" + "x")
    assert e.value.status == 400


def test_a_folder_that_is_a_link_is_refused(env, tmp_path):
    clone = tmp_path / "clone"
    clone.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "puffpai.json").write_text("{}", encoding="utf-8")
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(clone / "pkg"))
    else:
        os.symlink(outside, clone / "pkg", target_is_directory=True)
    try:
        with pytest.raises(AddError) as e:
            store_subfolder(clone, "pkg")
        assert "link" in e.value.problems[0]
    finally:
        os.rmdir(clone / "pkg") if os.name == "nt" else os.unlink(clone / "pkg")
    assert (outside / "puffpai.json").is_file()


def test_a_link_in_the_repo_is_never_followed(env, drawbridge):
    """A git link (mode 120000) named as the row's folder, pointing out of the clone: refused, staging empty."""
    blob = subprocess.run(["git", "-C", str(env.repo.path), "hash-object", "-w", "--stdin"], input="../..",
                          capture_output=True, text=True, check=True).stdout.strip()
    _git(env.repo.path, "update-index", "--add", "--cacheinfo", f"120000,{blob},escape")
    _git(env.repo.path, "commit", "-q", "-m", "a link")
    c3 = _git(env.repo.path, "rev-parse", "HEAD")
    _index(env.official, [_row(c3, path="escape")])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert "link" in e.value.problems[0] or "no folder" in e.value.problems[0]
    assert _staging(env.core) == []


def test_a_missing_commit_is_refused_and_staging_cleaned(env, drawbridge):
    _index(env.official, [_row("1" * 40)])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert "has no commit 111111111111" in e.value.problems[0]
    assert _staging(env.core) == [] and env.core.registry.get("hello-tool") is None


def test_a_head_that_is_not_the_pin_is_refused_and_staging_cleaned(env, drawbridge, monkeypatch):
    _index(env.official, [_row(env.repo.c1)])
    real = type(env.pm)._git

    def lying(self, git, where, *args, **kw):
        got = real(self, git, where, *args, **kw)
        if args[:1] == ("rev-parse",):
            got = subprocess.CompletedProcess(got.args, 0, stdout=env.repo.c2 + "\n", stderr="")
        return got
    monkeypatch.setattr(type(env.pm), "_git", lying)
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert "different commit than the store pins" in e.value.problems[0]
    assert _staging(env.core) == []


# -- 5. The manifest must be the row's package -----------------------------------------------------------------

@pytest.mark.parametrize("change, words", [({"id": "hello-two"}, "says 'hello-tool' (tool), but the store row is "
                                                                  "for 'hello-two' (tool)"),
                                           ({"kind": "app"}, "but the store row is for 'hello-tool' (app)")])
def test_a_manifest_that_is_not_the_rows_package_is_refused(env, drawbridge, change, words):
    _index(env.official, [_row(env.repo.c1, **change)])
    with pytest.raises(AddError) as e:
        env.pm.add(f"store:{change.get('id', 'hello-tool')}")
    assert words in e.value.problems[0]
    assert _staging(env.core) == []


# -- 6. Yanked ----------------------------------------------------------------------------------------------------

def test_a_yanked_commit_is_refused_with_its_reason(env, drawbridge):
    _index(env.official, [_row(env.repo.c1)], yanked=[{"id": "hello-tool", "commit": env.repo.c1,
                                                       "why": "It wrote notes outside its folder."}])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert e.value.status == 409 and "It wrote notes outside its folder." in e.value.problems[0]
    assert _staging(env.core) == []


def test_the_official_yank_list_counts_for_a_second_source(env, drawbridge):
    _index(env.official, [], yanked=[{"id": "hello-tool", "commit": env.repo.c1, "why": "Pulled."}])
    _second(env, [_row(env.repo.c1, tier="community")])
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert "Pulled." in e.value.problems[0]


def test_an_installed_package_that_is_yanked_gets_one_notice_and_stays(env, drawbridge):
    _index(env.official, [_row(env.repo.c1)])
    assert yes(env.core, env.pm.add("store:hello-tool"))["status"] == "installed"
    _index(env.official, [_row(env.repo.c1)], yanked=[{"id": "hello-tool", "commit": env.repo.c1,
                                                       "why": "It wrote notes outside its folder."}])
    notices = env.core.notices
    env.store.listing(notices=notices)
    env.store.updates(notices=notices)
    mine = [n for n in notices.list() if n["title"] == "Hello Tool was pulled from the store"]
    assert len(mine) == 1 and "puffpai remove hello-tool" in mine[0]["text"]
    assert "Nothing was removed" in mine[0]["text"]
    assert env.core.registry.get("hello-tool") is not None              # nothing removed without her Yes


# -- 7. An unreachable source -------------------------------------------------------------------------------------

def test_an_unreachable_source_says_so_and_lists_its_saved_copy(env, monkeypatch):
    body = json.dumps({"schema": "puffpai-store-index/1", "name": "Hers", "packages": [_row(env.repo.c1,
                                                                                            tier="community")]})
    monkeypatch.setattr(store_index, "http_get", lambda url, **kw: body.encode())
    env.store.add_source(HTTPS_SOURCE)
    first = env.store.listing(fresh=True)
    assert [r["id"] for r in first["packages"]] == ["hello-tool"] and first["sources"][1]["stale"] is False

    def down(url, **kw):
        raise store_index.Unreachable("it could not be reached (no network, or the address is wrong)")
    monkeypatch.setattr(store_index, "http_get", down)
    again = env.store.listing(fresh=True)
    src = again["sources"][1]
    assert [r["id"] for r in again["packages"]] == ["hello-tool"]
    assert src["stale"] is True and src["ok"] is True
    assert src["line"].startswith(f"Could not reach {HTTPS_SOURCE}: it could not be reached")
    assert "may be out of date" in src["line"]


def test_an_unreachable_source_with_no_saved_copy_lists_nothing_plainly(env, monkeypatch):
    def down(url, **kw):
        raise store_index.Unreachable("it answered 404")
    monkeypatch.setattr(store_index, "http_get", down)
    env.store.add_source(HTTPS_SOURCE)
    got = env.store.listing(fresh=True)
    assert got["packages"] == [] and got["sources"][1]["ok"] is False
    assert "it answered 404" in got["sources"][1]["line"] and "no saved copy" in got["sources"][1]["line"]
    with pytest.raises(AddError) as e:
        env.pm.add("store:hello-tool")
    assert e.value.status == 404 and any("it answered 404" in p for p in e.value.problems)


class _FakeResponse:
    def __init__(self, body, status=200, url=HTTPS_SOURCE):
        self.body, self.status, self.url = body, status, url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return self.url

    def read(self, n=-1):
        out, self.body = (self.body, b"") if n < 0 else (self.body[:n], self.body[n:])
        return out


@pytest.mark.parametrize("response, words", [
    (_FakeResponse(b"x" * (store_index.MAX_INDEX_BYTES + 10)), "over 2 MB"),
    (_FakeResponse(b"{}", status=203), "it answered 203"),
    (_FakeResponse(b"{}", url="http://store.example.invalid/i.json"), "not https")])
def test_the_get_has_a_size_cap_and_says_why_it_failed(monkeypatch, response, words):
    import urllib.request
    opener = SimpleNamespace(open=lambda req, timeout: response)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opener)
    with pytest.raises(store_index.Unreachable) as e:
        store_index.http_get(HTTPS_SOURCE)
    assert words in str(e.value)


def test_a_real_redirect_to_plain_http_is_not_followed():
    import urllib.request
    handler = store_index._HttpsRedirectsOnly()
    req = urllib.request.Request("https://store.example.invalid/index.json")
    assert handler.redirect_request(req, None, 302, "Found", {}, "http://store.example.invalid/i.json") is None


# -- 8. A bad row is dropped, the others still list --------------------------------------------------------------

def test_bad_rows_are_dropped_and_the_rest_list(env):
    good = _row(env.repo.c1)
    bad = [dict(good, id="Bad_Id"), dict(good, id="gitlab", repo="https://gitlab.com/a/b"),
           dict(good, id="short", commit="abc123"), dict(good, id="gold", tier="gold"),
           dict(good, id="setup", kind="wizard"), dict(good, id="twice"), dict(good, id="twice"),
           dict(good, id="reviewed", verified_commit="2" * 40), "not a row"]
    _index(env.official, bad[:5] + [good] + bad[5:])
    got = env.store.listing()
    assert [r["id"] for r in got["packages"]] == ["hello-tool", "twice"]
    dropped = got["sources"][0]["dropped"]
    assert len(dropped) == 8, dropped
    assert any(d.startswith("gitlab: repo must be") for d in dropped)
    assert any(d.startswith("twice: the same id") for d in dropped)


def test_a_file_that_is_not_an_index_falls_back_plainly(env):
    (env.official / "index.json").write_text(json.dumps({"schema": "something-else", "packages": []}),
                                             encoding="utf-8")
    got = env.store.listing()
    assert got["sources"][0]["ok"] is False and "not a PuffPAI store index" in got["sources"][0]["line"]


def test_row_text_is_kept_plain():
    index, _dropped = store_index.parse_index({"schema": "puffpai-store-index/1", "packages": [
        _row("a" * 40, name="Hello\u202e\u0007 Tool" + "x" * 200, icon="javascript:alert(1)")]})
    row = index["packages"][0]
    assert row["name"].startswith("Hello Tool") and len(row["name"]) == 80 and "icon" not in row


# -- 9. Updates ---------------------------------------------------------------------------------------------------

def test_updates_list_a_newer_pin_and_apply_with_a_yes(env, drawbridge):
    _index(env.official, [_row(env.repo.c1)])
    assert yes(env.core, env.pm.add("store:hello-tool"))["status"] == "installed"
    assert env.store.updates()["updates"] == []
    _index(env.official, [_row(env.repo.c2, version="1.1.0", tier="community")])
    ups = env.store.updates()["updates"]
    assert [(u["id"], u["have"]["commit"], u["store"]["commit"], u["store"]["origin"]) for u in ups] == [
        ("hello-tool", env.repo.c1, env.repo.c2, "store:community")]
    staged = env.pm.update("hello-tool")
    assert staged["card"]["origin"] == "store:community"
    assert staged["card"]["store"]["was_commit"] == env.repo.c1 and staged["card"]["store"]["commit"] == env.repo.c2
    assert env.core.registry.get("hello-tool")["store"]["commit"] == env.repo.c1      # nothing before her Yes
    assert yes(env.core, staged)["status"] == "installed"
    row = env.core.registry.get("hello-tool")
    assert row["version"] == "1.1.0" and row["origin"] == "store:community"
    assert row["store"]["commit"] == env.repo.c2
    assert (env.core.state / "packages" / "hello-tool" / "new.txt").is_file()
    assert env.store.updates()["updates"] == []
    with pytest.raises(AddError) as e:
        env.pm.update("hello-tool")
    assert e.value.status == 409 and "already at the commit" in e.value.problems[0]


def test_a_removed_source_gives_no_update(env, drawbridge):
    second = _second(env, [_row(env.repo.c1, tier="community")])
    assert yes(env.core, env.pm.add("store:hello-tool"))["status"] == "installed"
    env.store.remove_source(str(second))
    got = env.store.updates()
    assert got["updates"] == [] and "was removed" in got["not_found"][0]["line"]


# -- API and CLI --------------------------------------------------------------------------------------------------

def test_store_routes(env):
    client = env.core.app.test_client()
    owner = {"Authorization": f"Bearer {env.core.tokens.issue_owner_key()}"}
    device = {"Authorization": f"Bearer {env.core.tokens.issue_device('test laptop')}"}
    _index(env.official, [_row(env.repo.c1)])
    second = _index(env.tmp / "second", [_row(env.repo.c1, id="hello-two", tier="verified")])
    assert client.get("/v1/store").status_code == 401
    r = client.post("/v1/store/sources", json={"source": str(second)}, headers=owner)
    assert r.status_code == 403                                           # an API key can't add a source
    r = client.post("/v1/store/sources", json={"source": str(second)}, headers=device)
    assert r.status_code == 201, r.json
    r = client.get("/v1/store", headers=owner)
    assert r.status_code == 200
    assert [(p["id"], p["origin"]) for p in r.json["packages"]] == [("hello-tool", "store:verified"),
                                                                     ("hello-two", "store:community")]
    assert [s["official"] for s in client.get("/v1/store/sources", headers=owner).json["sources"]] == [True, False]
    r = client.delete("/v1/store/sources", json={"source": str(env.official)}, headers=device)
    assert r.status_code == 409
    r = client.post("/v1/store/sources", json={"source": "http://plain.example/index.json"}, headers=device)
    assert r.status_code == 400
    assert client.get("/v1/store/updates", headers=owner).json["updates"] == []
    from urllib.parse import quote
    r = client.delete("/v1/store/sources?source=" + quote(str(second), safe=""), headers=device)
    assert r.status_code == 200 and r.json["removed"] is True
    r = client.post("/v1/packages", json={"source": "store:hello-tool"}, headers=owner)
    assert r.status_code == 202 and r.json["card"]["origin"] == "store:verified"


def test_cli_store_without_a_core(env, capsys):
    from puffpai_core import cli
    _index(env.official, [_row(env.repo.c1)])
    second = _index(env.tmp / "second", [])
    assert cli.main(["store", "add-source", str(second)]) == 0
    assert cli.main(["store", "sources"]) == 0
    assert cli.main(["store", "list"]) == 0
    assert cli.main(["store", "remove-source", str(env.official)]) == 1
    assert cli.main(["store", "updates"]) == 0
    out = capsys.readouterr().out
    assert "official" in out and str(second) in out and "hello-tool" in out and "verified" in out
    assert "can't be removed" in out and "No updates from the store." in out
    assert "\u2014" not in out
