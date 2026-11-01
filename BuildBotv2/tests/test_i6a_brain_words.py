"""Lane I6a: the brain and Toolsmith's search, from the third live fresh-install proof (2026-10-23, qwen3.5:9b).

1 reasoning never reaches the owner or the chat history; 2 no claim of work when no tool ran; 3 FreshTable's
sample asks are real requests; 4 a thin or odd search phrase no longer hides WorldMonitor; 5 the Prove
step's example comes from the tool the job uses, and "works" is said once.

Nothing here reaches the internet: GitHub's answers are hand-made fixtures (tests/data/worldmonitor), and the
one model server is a fake on a free port of this computer.
"""

import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from toolsmith_fakes import FakePorts
from zone2_doubles import FakeRuntime, call, make_brain
from puffpai_core.packages import check
from puffpai_core.wizard.lines import prove_lines
from puffpai_core.zone2 import loop
from puffpai_core.zone2 import settings as ms
from puffpai_core.zone2.ollama import OllamaRuntime, ThinkFilter, strip_thinking
from puffpai_core.zone3.toolsmith import agent, find

ROOT = Path(__file__).resolve().parents[1]
FRESHTABLE = json.loads((ROOT / "packages" / "freshtable" / "puffpai.json").read_text(encoding="utf-8"))
WM = Path(__file__).parent / "data" / "worldmonitor"
RAW = "https://raw.githubusercontent.com/"
SEARCH = "https://api.github.com/search/repositories?*"
RUNS = "## Quick start\n```bash\nnpm install\nnpm start\n```\n"


# -- 1. reasoning is never the answer ------------------------------------------------------------------
@pytest.mark.parametrize("pieces, want", [
    (["<think>The owner wants me to add it.</think>", "Done."], "Done."),
    (["<thi", "nk>plan it</th", "ink>\n\nHello."], "Hello."),            # tags split across pieces
    (["Hi ", "<think>x</think>", "there."], "Hi there."),
    (["<think>never closed, all reasoning"], ""),                          # an unclosed block is dropped
    (["a < b and <thin"], "a < b and <thin"),                              # a half tag at the end is words
    # A closing tag with no opening one: everything before it was reasoning (live qwen3.5:9b, 10-23).
    (["Blue light scatters most.\n</think>\n\nBlue light scatters most."], "Blue light scatters most."),
    (["Blue light", " scatters.\n</th", "ink>\n\nBlue light scatters."], "Blue light scatters."),
    (["<THINK >plan</ Think>Hi."], "Hi."),                                   # any case, spaces in the tag
    (["<thinking>plan it</thinking>Yo."], "Yo."),
    (["reasoning</thinking>Answer."], "Answer."),
])
def test_think_blocks_are_taken_out_even_in_pieces(pieces, want):
    f = ThinkFilter()
    for p in pieces:
        f.feed(p)
    f.end()
    assert f.text() == want
    assert strip_thinking("".join(pieces)) == want


def test_a_stray_closing_tag_never_reaches_the_history(state, freshtable):
    rt = FakeRuntime({"text": "Blue light scatters most.\n</think>\n\nBlue light scatters most."})
    brain, _, _ = make_brain(state, freshtable, rt)
    assert brain.turn("why is the sky blue? one sentence")["text"] == "Blue light scatters most."
    stored = [json.loads(x) for x in (state / "chat" / "main.jsonl").read_text(encoding="utf-8").splitlines()]
    assert stored[-1]["content"] == "Blue light scatters most."


class _Ollama(BaseHTTPRequestHandler):
    lines: list = []
    bodies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        type(self).bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}"))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for line in self.lines:
            self.wfile.write((json.dumps(line) + "\n").encode())


@pytest.fixture
def thinking_ollama():
    _Ollama.bodies = []
    _Ollama.lines = [{"message": {"role": "assistant", "thinking": "The owner wants me to add a tool"}},
                     {"message": {"content": "<think>still reasoning"}}, {"message": {"content": "</think>"}},
                     {"message": {"content": "Toolsmith is on it."}},
                     {"message": {"tool_calls": [{"function": {"name": "ask_toolsmith", "arguments": {"job": "x"}}}]}},
                     {"done": True, "done_reason": "stop"}]
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def test_the_adapter_never_shows_or_returns_reasoning(thinking_ollama):
    rt = OllamaRuntime(thinking_ollama, timeout_s=5)
    words = []
    got = rt.chat([{"role": "user", "content": "add x"}], model="qwen3.5:9b", think=True, on_token=words.append)
    assert got["text"] == "Toolsmith is on it." and "".join(words).strip() == "Toolsmith is on it."
    assert "owner wants" not in json.dumps(got) and got["tool_calls"][0]["name"] == "ask_toolsmith"
    assert _Ollama.bodies[-1]["think"] is True


def test_qwen35_thinks_in_chat_only(state):
    s = ms.save(state, model="qwen3.5:9b")
    assert s.think() is False and s.think_in_chat() is True        # JSON asks and Toolsmith's turns stay fast
    s = ms.save(state, model="qwen2.5:7b-instruct")
    assert s.think() is False and s.think_in_chat() is False


def test_a_chat_turn_on_qwen35_asks_with_thinking_and_keeps_only_the_answer(state, freshtable):
    rt = FakeRuntime({"text": "<think>The owner wants a joke.</think>Why did the hen cross the road?"})
    brain, _, _ = make_brain(state, freshtable, rt, settings={"model": "qwen3.5:9b"})
    got = brain.turn("tell me a joke")
    assert rt.calls[0]["think"] is True
    assert got["text"] == "Why did the hen cross the road?"
    stored = (state / "chat" / "main.jsonl").read_text(encoding="utf-8")
    assert "owner wants" not in stored and "hen cross" in stored


# -- 2. no claim of work when no tool ran ------------------------------------------------------------
# The verifier's corpus (C:\tmp\v6a\s\claims.py, 10-23): every one of these is a claim of this bot's own work.
VERIFIER_CLAIMS = [
    "Toolsmith is building the world news dashboard app now.", "I've added milk to your list.",
    "I've set up the reminder.", "It's on your list now.", "Done!", "Done, it's added.",
    "Added it to your shopping list.", "Your reminder has been scheduled.", "The app was installed.",
    "I'm adding it now.", "I'm building that for you.", "I made the app.", "I've queued the build.",
    "Your app is ready to use.", "Toolsmith is on it.", "I've asked Toolsmith to build it.",
    "I've added eggs to the list, let me know if you need more.", "I've added eggs, no problem.",
    "Got it, milk is now on the shopping list.", "I saved your recipe.", "Your note is saved.", "Saved!",
    "All set, the app is installed and running.", "I've installed it and it is running",
    "I have scheduled it for 7am", "Sure! I've created a new app called News.", "Milk added to the list.",
    "I went ahead and added it.", "Toolsmith has started building it.", "Toolsmith started the build."]
# ...and none of these is.
VERIFIER_ORDINARY = [
    "Toolsmith can build apps for you.", "I added nothing.", "Want me to ask Toolsmith to add it?",
    "The Eiffel Tower was built in 1889.", "The pyramids were built by thousands of workers.",
    "Let me put it this way: a dashboard shows many things at once.",
    "Let me make a joke: why did the chicken cross the road?", "The market is building momentum this year.",
    "Rome wasn't built in a day.", "Bread is made from flour.", "Python was created by Guido van Rossum.",
    "The first iPhone was released in 2007 and was built by Foxconn.", "I'll explain how it works.",
    "The list is saved in your data folder.", "Here's a joke: I'm reading a book on anti-gravity.",
    "Sourdough is started with a culture of wild yeast.", "The meeting was scheduled for Monday by the board.",
    "I can add it if you like.", "Your plants are building roots.", "Let me start with the basics.",
    "We have added sugar in the recipe list below."]
# Lane I6a's own: replies seen live from qwen3.5:9b and qwen2.5:7b-instruct, and near misses.
OWN_CLAIMS = [
    "Toolsmith is building the world news dashboard app now. Once it's done, the result will be shared here.",
    "I will ask Toolsmith to add an app for a world news dashboard.", "I've added a request for a world news app.",
    "Milk has been added to the shopping list.", "I'll add a world news dashboard app for you now.",
    "Your dinner is saved for Tuesday.", "I will add an app for a world news dashboard using Toolsmith.",
    "This tool was built by Toolsmith and is now ready to use.",
    "The World News Dashboard is still being built by Toolsmith.", "Toolsmith is making a plan to add it.",
    "I've put milk on your shopping list.", "Your meal for Tuesday is planned.", "Okay, I've booked it.",
    "**Done!** Milk is on the list.", "The recipe has been saved."]
OWN_ORDINARY = [
    "There are 3 things on your shopping list: milk, eggs and bread.", "Nothing was added.",
    "I have not added anything yet.", "I didn't add anything.", "To save a recipe, tell me its name.",
    "Hello! How can I help you today?", "I made a typo, sorry.", "Toolsmith adds apps after your Yes.",
    "Milk is on your shopping list.", "The app was built with React by its makers.",
    "You can say done when you finish.", "Your list has 3 items.", "The weather app shows rain today.",
    "Scattering of sunlight makes the sky look blue."]


@pytest.mark.parametrize("text", VERIFIER_CLAIMS + OWN_CLAIMS)
def test_claims_of_work_are_seen(text):
    assert loop.claims_work(text)


@pytest.mark.parametrize("text", VERIFIER_ORDINARY + OWN_ORDINARY)
def test_ordinary_answers_are_not_claims(text):
    assert not loop.claims_work(text)


def test_a_read_only_tool_never_excuses_an_added_claim(state, freshtable):
    rt = FakeRuntime(call("freshtable__show_list"), {"text": "I've added milk to your list."},
                     {"text": "Your list is empty."})
    brain, items, _ = make_brain(state, freshtable, rt)
    got = brain.turn("put milk on the list")
    assert items == [] and got["text"] == "Your list is empty." and loop.NO_TOOL_NOTE in rt.seen(2)


def test_a_reminder_from_chat_is_a_tool_that_acts(state, freshtable):
    rt = FakeRuntime(call("checklist_add", title="Water the plants", why="She asked"),
                     {"text": "Done! It's on your Checklist."})
    brain, _, _ = make_brain(state, freshtable, rt, checklist=types.SimpleNamespace(
        add=lambda step, package, trust=None, raised_by=None: {"id": "s1", "title": step["title"]}))
    got = brain.turn("remind me to water the plants")
    assert got["text"] == "Done! It's on your Checklist." and len(rt.calls) == 2


def test_a_claim_with_no_tool_is_asked_again_and_then_the_tool_runs(state, freshtable):
    rt = FakeRuntime({"text": "Toolsmith is building the world news dashboard app now."},
                     call("ask_toolsmith", job="add an app for a world news dashboard"),
                     {"text": "Toolsmith made a plan: say Yes on its card."})
    brain, _, _ = make_brain(state, freshtable, rt)
    asked = []
    brain.toolsmith = lambda job, ctx: (asked.append(job), {"ok": True, "said": "A plan card is shown."})[1]
    got = brain.turn("add an app for a world news dashboard")
    assert asked == ["add an app for a world news dashboard"]
    assert got["used"] == ["ask_toolsmith"] and got["text"] == "Toolsmith made a plan: say Yes on its card."
    assert loop.NO_TOOL_NOTE in rt.seen(1)
    stored = (state / "chat" / "main.jsonl").read_text(encoding="utf-8")
    assert "is building" not in stored                               # the false claim is never kept


def test_a_claim_twice_becomes_the_honest_line(state, freshtable):
    rt = FakeRuntime({"text": "Toolsmith is building it now."}, {"text": "I've added the app for you."})
    brain, _, _ = make_brain(state, freshtable, rt)
    got = brain.turn("add an app for a world news dashboard")
    assert got["text"] == loop.NO_TOOL_LINE and got["used"] == [] and len(rt.calls) == 2
    stored = [json.loads(x) for x in (state / "chat" / "main.jsonl").read_text(encoding="utf-8").splitlines()]
    assert stored[-1]["content"] == loop.NO_TOOL_LINE


def test_an_ordinary_answer_is_asked_once_and_kept_as_said(state, freshtable):
    rt = FakeRuntime({"text": "FreshTable keeps your pantry and shopping list."})
    brain, _, _ = make_brain(state, freshtable, rt)
    got = brain.turn("what does FreshTable do?")
    assert got["text"] == "FreshTable keeps your pantry and shopping list." and len(rt.calls) == 1


def test_a_claim_after_a_tool_ran_is_left_alone(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "Milk has been added to the list."})
    brain, items, _ = make_brain(state, freshtable, rt, consent=lambda _q: True)
    got = brain.turn("put milk on the shopping list")
    assert items == ["milk"] and got["text"] == "Milk has been added to the list." and len(rt.calls) == 2


def test_a_check_is_never_asked_again(state, freshtable):
    rt = FakeRuntime({"text": "I've added milk."})
    brain, _, _ = make_brain(state, freshtable, rt)
    got = brain.sample_ask("freshtable.add_to_list", "put milk on the shopping list")
    assert got["ok"] is False and len(rt.calls) == 1                 # item 12 measures the first pick only


def test_the_prompt_says_each_add_needs_its_own_call():
    assert "Each new ask to add something needs its own ask_toolsmith call" in loop.TOOL_NOTE


# -- 3. FreshTable's sample asks are real requests ------------------------------------------------------
def test_every_freshtable_tool_has_a_real_sample_ask():
    import re
    server = (ROOT / "packages" / "freshtable" / "server.py").read_text(encoding="utf-8")
    tools = re.findall(r'^    \("([a-z_]+)", "', server, flags=re.M)
    examples = FRESHTABLE["abilities"]["examples"]
    assert len(tools) == len(FRESHTABLE["abilities"]["tools"]) == 18 and sorted(examples) == sorted(tools)
    for name, asks in examples.items():
        assert asks and len(asks[0].split()) >= 4, name          # a request with its details, not a description


def test_item_12_says_how_to_fix_a_tool_with_no_sample_ask(monkeypatch, tmp_path):
    monkeypatch.setattr(check, "_check_brain", lambda *a: object())
    monkeypatch.setattr(check.seams, "find", lambda name: (lambda tool, ask, brain=None: {
        "ok": False, "reason": "The brain answered in words and called no tool."}))
    data = {"id": "pkg", "abilities": {"examples": {"a": ["put milk on the shopping list"]}}}
    wrong, why = check._sample_asks(data, [{"name": "a", "description": "A."}, {"name": "b", "description": "B."}],
                                    None, None, tmp_path)
    assert why == "" and wrong["a"] == "The brain answered in words and called no tool."
    assert wrong["b"].startswith("The brain answered in words and called no tool. It has no sample ask in "
                                 "abilities.examples")


# -- 4. a thin or odd phrase no longer hides WorldMonitor ------------------------------------------------
def _dash_ports(tmp_path):
    files = {"rss+news+reader": "search_rss_news_reader.json", "api+news+display": "search_api_news_display.json",
             "world+news": "search_world_news.json", "news+dashboard": "search_news_dashboard.json"}
    asked: list[str] = []

    def search(url):
        q = url.split("?q=", 1)[1].split("+in:", 1)[0]
        asked.append(q)
        name = files.get(q)
        return (200, (WM / name).read_text(encoding="utf-8")) if name else (200, '{"items": []}')

    def readme(url):
        if "/koala73/worldmonitor/" in url:
            return 200, (WM / "README.fixture.md").read_text(encoding="utf-8")
        return 200, "# A project\n" + RUNS
    ports = FakePorts(tmp_path, web={SEARCH: search, RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md":
                                     (200, "# none\n"), RAW + "*": readme})
    return ports, asked


DASH = {"n": 0, "name": "World news dashboard", "does": "add an app for a world news dashboard", "sample_ask": "",
        "pasted": []}
PROOF_PHRASES = [["rss", "news", "reader"], ["api", "news", "display"]]      # what the brain said in the proof


def test_the_brains_phrases_alone_miss_worldmonitor():
    for name in ("search_rss_news_reader.json", "search_api_news_display.json"):
        items = json.loads((WM / name).read_text(encoding="utf-8"))["items"]
        assert "koala73/worldmonitor" not in [i["full_name"] for i in items]


def test_the_brains_phrases_then_their_thin_parts_within_the_cap(tmp_path):
    ports, asked = _dash_ports(tmp_path)
    f = find.Finder(ports, tmp_path / "c")
    plan = f.plan_job(DASH, PROOF_PHRASES, [], {})
    # D88: "api news display" found 3 (thin), so the round is thin: the job name's pairs come first in round
    # two, then each thin 3-word phrase's 2-word parts (D85).
    assert asked == ["rss+news+reader", "api+news+display", "world+news", "news+dashboard", "api+news",
                     "news+display"]
    assert f.searches == 6                                                    # within the cap of 10 (lane I15)
    assert "github:koala73/worldmonitor" in [c["ref"] for c in plan["candidates"]]


def test_each_job_gets_its_share_of_the_searches(tmp_path):
    ports, asked = _dash_ports(tmp_path)
    f = find.Finder(ports, tmp_path / "c")
    f.jobs_left = 2
    f.plan_job(DASH, PROOF_PHRASES + [["world", "news", "map"], ["global", "news", "feed"]], [], {})
    assert asked == ["rss+news+reader", "api+news+display", "world+news+map", "global+news+feed",
                     "world+news"]                    # 10 searches over 2 jobs (lane I15); the name pair first (D88)
    f.jobs_left = 1
    f.plan_job({**DASH, "n": 1, "name": "News map"}, [["news", "map"]], [], {})
    assert asked[5:] == ["news+map"] and f.searches == 6                       # a 2-word name is one phrase


def test_no_brain_searches_the_job_names_pairs(tmp_path):
    ports, asked = _dash_ports(tmp_path)                                     # no brain: brain_json answers None
    f = find.Finder(ports, tmp_path / "c")
    phrases = f.search_words([DASH], "a world news dashboard")
    assert phrases == {0: [["world", "news"], ["news", "dashboard"]]}         # the fallback: the name's pairs
    plan = f.plan_job(DASH, phrases[0], [], {})
    assert asked == ["world+news", "news+dashboard"]
    assert plan["candidates"][0]["ref"] == "github:koala73/worldmonitor"


# -- 4b. the job name's pairs when the brain's round comes back thin (D88) ---------------------------------
def _counted_ports(tmp_path, counts):
    """GitHub answers each query with ``counts[query]`` made-up projects (0 when not listed)."""
    asked: list[str] = []

    def search(url):
        q = url.split("?q=", 1)[1].split("+in:", 1)[0]
        asked.append(q)
        items = [{"full_name": f"maker{n}/{q.replace('+', '-')}", "name": q.replace("+", "-"),
                  "description": q.replace("+", " "), "stargazers_count": 50} for n in range(counts.get(q, 0))]
        return 200, json.dumps({"items": items})
    ports = FakePorts(tmp_path, web={SEARCH: search, RAW + "awesome-selfhosted/awesome-selfhosted/master/README.md":
                                     (200, "# none\n")})
    return ports, asked


def test_a_round_that_is_not_thin_adds_no_name_pairs(tmp_path):
    ports, asked = _counted_ports(tmp_path, {"rss+news+reader": 9, "news+aggregator": 7})
    f = find.Finder(ports, tmp_path / "c")
    f.search_job(DASH, [["rss", "news", "reader"], ["news", "aggregator"]])
    assert asked == ["rss+news+reader", "news+aggregator"]


def test_a_name_pair_the_brain_already_said_is_not_searched_twice(tmp_path):
    ports, asked = _counted_ports(tmp_path, {})
    f = find.Finder(ports, tmp_path / "c")
    f.search_job(DASH, [["world", "news"], ["headline", "board"]])            # both thin
    assert asked == ["world+news", "headline+board", "news+dashboard"]
    assert f.skipped == 0


def test_the_name_pairs_are_screened_first_and_a_held_one_is_never_searched(tmp_path):
    ports, asked = _counted_ports(tmp_path, {})
    ports.household_names = lambda: ["Rose"]
    events: list = []
    screen = ports.screen_queries

    def screen_queries(queries):
        events.append(("screen", list(queries), len(asked)))
        return screen(queries)
    ports.screen_queries = screen_queries
    f = find.Finder(ports, tmp_path / "c")
    job = {"n": 0, "name": "Rose garden planting", "does": "plan the garden", "sample_ask": "", "pasted": []}
    f.search_job(job, [["seed", "schedule"]])
    assert len(events) == 1 and events[0][2] == 0                            # one ask, before any search
    assert {"rose garden", "garden planting"} <= set(events[0][1])
    assert asked == ["seed+schedule", "garden+planting"]                     # "rose garden" was held
    assert not any("rose" in q for q in asked)


@pytest.mark.parametrize("name, brain, pairs", [
    ("Recipe box tracker", [["meal", "cards"]], ["recipe+box", "box+tracker"]),
    ("Garden planting calendar", [["seed", "schedule"]], ["garden+planting", "planting+calendar"]),
    ("Soccer drills tracker", [["coach", "notes", "app"]], ["soccer+drills", "drills+tracker"]),
])
def test_any_job_with_thin_brain_words_gets_its_name_pairs(tmp_path, name, brain, pairs):
    ports, asked = _counted_ports(tmp_path, {})
    f = find.Finder(ports, tmp_path / "c")
    f.search_job({"n": 0, "name": name, "does": "", "sample_ask": "", "pasted": []}, brain)
    first = ["+".join(b) for b in brain]
    assert asked[:len(first)] == first and asked[len(first):len(first) + 2] == pairs


# -- 5. the Prove step's words ------------------------------------------------------------------------
class _Link:
    def __init__(self, called):
        self.called, self.status = called, []

    def fits(self, package, ask):
        return {"ok": True, "asked": True, "package": package, "called": self.called, "reason": ""}

    def job_status(self, job_id, **fields):
        self.status.append(fields)
        return {}


def _reuse_line(called, job):
    b = agent.Builder.__new__(agent.Builder)
    b.link = _Link(called)
    row = {"id": "freshtable", "name": "FreshTable", "status": "running", "health": {"ok": True},
           "examples": FRESHTABLE["abilities"]["examples"]}
    b._prove_tools({"id": "j1", "plan": {}, **job}, row)
    return b.link.status[-1]["line"]


def test_the_example_is_the_one_for_the_tool_the_job_uses():
    line = _reuse_line(["freshtable.list_meal_plan"], {"name": "Weekly meal planner",
                                                       "does": "Suggests dinner plans for the next seven days"})
    assert 'for example: "what dinners are planned this week"' in line and "milk" not in line


def test_no_example_when_none_is_about_the_job():
    b = agent.Builder.__new__(agent.Builder)
    b.link = _Link(["pkg.other"])
    row = {"id": "pkg", "name": "Pkg", "status": "running", "health": {"ok": True},
           "examples": {"tick": ["tick the eggs off"]}}
    b._prove_tools({"id": "j1", "plan": {}, "name": "Weather", "does": "say the weather"}, row)
    assert b.link.status[-1]["line"].endswith("Ask the chat to use it.")


def test_a_working_app_says_works_once(monkeypatch):
    b = agent.Builder.__new__(agent.Builder)
    b.link = _Link([])
    b.link.registry = lambda: {"rows": [{"id": "flask-web-app-tutorial", "name": "Flask-Web-App-Tutorial",
                                         "port": 5000}]}
    monkeypatch.setattr(agent.prove, "prove", lambda *a: {"result": "working", "answer": "The page asks you to log in."})
    b._prove({"id": "j1", "name": "Flask-Web-App-Tutorial"}, "flask-web-app-tutorial",
             {"ui": {"pages": [{"url": "http://127.0.0.1:{port}/"}]}})
    line = b.link.status[-1]["line"]
    said = " ".join(prove_lines([{"name": "Flask-Web-App-Tutorial", "state": "working", "reason": line}]))
    assert said == "Flask-Web-App-Tutorial: works. The brain read Flask-Web-App-Tutorial: The page asks you to log in."
    assert said.count("works") == 1




# -- 6. chat raises reminders only, never a command to run (verifier, 10-23) -----------------------------
class _Steps(list):
    def add(self, step, package, trust=None, raised_by=None):
        self.append(step)
        return {"id": step["id"], "title": step.get("title", "")}


@pytest.mark.parametrize("kind", ["run_command", "open_url", "enter_secret"])
def test_chat_never_raises_a_step_that_runs_a_command(state, freshtable, kind):
    steps = _Steps()
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), checklist=steps)
    got = brain.call_tool("checklist_add", {"title": "Sky", "why": "She asked", "kind": kind})
    assert got["ok"] is False and steps == []
    got = brain.call_tool("checklist_add", {"title": "Sky", "why": "w", "kind": "confirm",
                                            "text": 'echo "Scattering of sunlight"', "shell": "bash"})
    assert got["ok"] is False and steps == []                      # no command text, no shell at all


def test_the_live_sky_question_step_is_refused_in_a_turn(state, freshtable):
    steps = _Steps()
    rt = FakeRuntime(call("checklist_add", title="Why the sky is blue", why="answer", kind="run_command",
                          shell="bash", text='echo "Scattering of sunlight..."'),
                     {"text": "Sunlight scatters, and blue scatters most."})
    brain, _, _ = make_brain(state, freshtable, rt, checklist=steps)
    got = brain.turn("why is the sky blue? one sentence")
    assert steps == [] and got["text"] == "Sunlight scatters, and blue scatters most."


def test_a_reminder_is_still_added_and_the_tool_says_what_it_is_for(state, freshtable):
    steps = _Steps()
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), checklist=steps)
    got = brain.call_tool("checklist_add", {"title": "Water the plants", "why": "She asked"})
    assert got["ok"] is True and steps[0]["kind"] == "confirm" and set(steps[0]) == {"id", "kind", "title", "why"}
    tool = next(t for t in brain.concierge(brain._checked()[1]).core_tools(lambda: None) if t.name == "checklist_add")
    assert "asks to be reminded" in tool.description and "Never for answering a question" in tool.description
    assert tool.schema["properties"]["kind"]["enum"] == ["confirm", "plug_in"]


# -- 7. the flaky main e2e: a try's page counts only from its own server (coordinator, 10-23) ------------
def test_try_ports_are_not_always_the_lowest_and_skip_listeners(monkeypatch):
    from puffpai_core.zone3.toolsmith_host import trials
    import puffpai_core.steward.ports as sp
    monkeypatch.setattr(sp, "listening_ports", lambda: set(range(18420, 18990)))
    assert 18990 <= trials.free_port() <= 18999                    # a port the listen table shows is never taken
    monkeypatch.setattr(sp, "listening_ports", lambda: set())
    assert len({trials.free_port() for _ in range(12)}) > 1        # random, so two runs do not meet on 18420


class _SlowOwnLink:
    """The page answers at once, but the try's own server listens there only from the third look."""

    def __init__(self):
        self.looks = 0

    def try_open(self, job_id, ref):
        return {"try_id": "t1", "port": 18431}

    def try_log(self, try_id):
        self.looks += 1
        own = [["127.0.0.1", 18431]] if self.looks >= 3 else []
        return {"log": "", "alive": 1, "listens": {"port": 18431, "own": own, "app_port": None, "exposed": [],
                                                    "taken": [], "said": ""}}

    def read_page(self, url):
        return {"ok": True, "title": "Home", "text": "Today's headlines and more words here"}


def _page_try(state, link, wait_s=5):
    from puffpai_core.zone3.toolsmith import tries
    from puffpai_core.zone3.toolsmith_host import settings as settings_mod
    st = settings_mod.read(state)
    st["timing"]["page_wait_s"], st["timing"]["poll_s"] = wait_s, 0.01
    t = tries.Try({"id": "j1", "name": "News"}, {"ref": "github:x/news", "name": "News", "url": "https://github.com/x/news"},
                  link=link, settings=st, hooks=tries.Hooks())
    return t


def test_wait_for_page_waits_until_the_page_is_the_trys_own(state):
    link = _SlowOwnLink()
    got = _page_try(state, link).t_wait_for_page({"url": "http://127.0.0.1:18431/"})
    assert got["ok"] and link.looks >= 3


def test_wait_for_page_says_when_another_program_serves_the_page(state):
    class Never(_SlowOwnLink):
        def try_log(self, try_id):
            got = super().try_log(try_id)
            got["listens"]["own"] = []
            return got
    got = _page_try(state, Never(), wait_s=0.2).t_wait_for_page({"url": "http://127.0.0.1:18431/"})
    assert not got["ok"] and "no program of this try listens there" in got["said"]
