"""The terminal chat matches its mockup (D86, docs/mockups/12_window_mockup.html, the Terminal view): the brain's
markdown drawn as it streams, plain tool lines, the header, the command search, the plan card, /watch and /window."""

from __future__ import annotations

import io
import json
import re
import sys
import types

import pytest

from puffpai_core import cli, termchat
from puffpai_core.zone2 import concierge

SCREENSHOT = ("**Puff** (`cloud-avatar`) - Avatar, stopped\n"
              "*Hand-drawn avatar, and it moves.*\n")
SAMPLE = """Here is **what I found**, with *some* _notes_ and `code_here`:

# A heading
## Another **one**

- first item
- second with **bold**
* a star item
1. one
2. two

> a quote with *words*

[the docs](https://example.com/a_b_c) and [y] yes and [same](same) and 2 * 3 and 2 ** 3 and 2**3 and 5*4*3.
cloud_avatar stays, __init__ stays, snake_case_name stays. **never closed
closed** with no start. \\*not italic\\* and a lone ` tick. *no close either

---

```python
x = 2 ** 3  # **not bold**
print(`a`)

```
Done, **all** of it.
"""


class Tty(io.StringIO):
    encoding = "utf-8"


def look(tty=False, vt=False, env=None, out=None):
    return termchat.Look(out or Tty(), env=env if env is not None else {}, tty=lambda s: tty, vt_on=lambda s: vt)


def color():
    return look(tty=True, vt=True)


def no_color():
    return look(tty=True, vt=True, env={"NO_COLOR": "1"})


def old_console():
    return look(tty=True, vt=False, out=io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="replace"))


def draw(text, the_look, cuts=()):
    got = []
    md = termchat.Markdown(the_look, got.append)
    at = 0
    for cut in cuts:
        md.feed(text[at:cut])
        at = cut
    md.feed(text[at:])
    md.end()
    return "".join(got)


def words_of(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# -- 1. markdown ------------------------------------------------------------------------------------------------
def test_her_screenshot_has_no_markers_in_either_look():
    plain = draw(SCREENSHOT, no_color())
    assert plain == "  Puff (cloud-avatar) - Avatar, stopped\n  Hand-drawn avatar, and it moves.\n"
    painted = draw(SCREENSHOT, color())
    assert painted == ("  \x1b[1mPuff\x1b[0m (\x1b[36mcloud-avatar\x1b[0m) - Avatar, stopped\n"
                       "  \x1b[3mHand-drawn avatar, and it moves.\x1b[0m\n")
    assert words_of(painted) == plain


@pytest.mark.parametrize("make", [color, no_color, old_console], ids=["color", "no_color", "old_console"])
@pytest.mark.parametrize("sample", [SCREENSHOT, SAMPLE, "***both*** and *a **b** c* and `` a`b `` end",
                                    "[open](http://x/a(b)c) [broken](no close", "  ", "\n\n", "*", "**", "`", "```", ">"],
                         ids=lambda s: repr(s[:12]))
def test_every_split_point_draws_the_same(make, sample):
    the_look = make()
    whole = draw(sample, the_look)
    for cut in range(len(sample) + 1):
        assert draw(sample, the_look, [cut]) == whole, cut
    assert draw(sample, the_look, range(1, len(sample))) == whole      # one character at a time


@pytest.mark.parametrize("seed", range(5))
def test_any_set_of_split_points_draws_the_same(seed):
    import random
    rng = random.Random(seed)
    the_look = color()
    whole = draw(SAMPLE, the_look)
    for _ in range(50):
        cuts = sorted(rng.sample(range(1, len(SAMPLE)), rng.randint(1, 60)))
        assert draw(SAMPLE, the_look, cuts) == whole


def test_the_sample_reads_right_with_no_color():
    the_look = no_color()
    the_look.width = 200                               # no wrapping here
    lines = draw(SAMPLE, the_look).splitlines()
    text = "\n".join(lines)
    assert "  Here is what I found, with some notes and code_here:" in lines
    assert "  A heading" in lines and "  Another one" in lines
    assert "  · first item" in lines and "  · second with bold" in lines and "  · a star item" in lines
    assert "  1. one" in lines and "  2. two" in lines
    assert "  │ a quote with words" in lines
    assert "the docs (https://example.com/a_b_c)" in text and "[y] yes" in text and " same and " in text
    assert "2 * 3 and 2 ** 3 and 2**3 and 5*4*3." in text
    assert "cloud_avatar stays, __init__ stays, snake_case_name stays. never closed" in text
    assert "  closed with no start. *not italic* and a lone ` tick. *no close either" in lines
    assert "x = 2 ** 3  # **not bold**" in text and "    print(`a`)" in lines     # code is left as it came
    assert "```" not in text and "  Done, all of it." in lines
    assert "  " + "─" * 20 in lines


def test_the_old_console_gets_a_plain_dash_and_only_ascii():
    text = draw("- one\n> two\n---\n", old_console())
    assert text == "  - one\n  | two\n  " + "-" * 20 + "\n"
    assert "\x1b" not in draw(SAMPLE, old_console())


def test_color_goes_on_and_off_around_the_marked_words_only():
    assert draw("a **b c** d", color()) == "  a \x1b[1mb c\x1b[0m d\n"
    assert draw("a *b* d", color()) == "  a \x1b[3mb\x1b[0m d\n"
    assert draw("word**bold**", no_color()) == "  wordbold\n" and draw("x**y and z", no_color()) == "  x**y and z\n"
    assert draw("# Head", color()) == "  \x1b[1mHead\x1b[0m\n"
    assert draw("[t](http://u)", color()) == "  t (\x1b[36mhttp://u\x1b[0m)\n"
    assert draw("**never closed\nnext", color()) == "  \x1b[1mnever closed\x1b[0m\n  next\n"   # ends with its line


def test_words_appear_as_they_stream_and_only_a_marker_or_word_waits():
    got = []
    md = termchat.Markdown(no_color(), got.append)
    md.feed("Hello there wor")
    assert "".join(got) == "  Hello there"             # "wor" may go on: it wraps whole
    md.feed("ld **bo")
    assert "".join(got) == "  Hello there world"
    md.feed("ld** and *ital")
    assert "".join(got) == "  Hello there world bold and"
    md.feed("ic* ok")
    assert "".join(got) == "  Hello there world bold and italic"
    md.end()
    assert "".join(got) == "  Hello there world bold and italic ok\n"


def test_an_escape_code_from_the_brain_never_gets_out_even_split():
    text = draw("hi \x1b[2J**bo\x1b]0;x\x07ld**‮", no_color(), [4, 9])
    assert text == "  hi [2Jbo]0;xld\n"


def test_long_lines_wrap_on_a_console_with_their_indent_and_stay_whole_in_a_pipe():
    the_look = no_color()
    the_look.width = 40
    text = draw("- " + "word " * 20, the_look)
    assert all(len(line) <= 40 for line in text.splitlines()) and text.count("\n") > 1
    assert text.splitlines()[1].startswith("    word")   # under the words, not the dot
    assert draw("word " * 40, look()).count("\n") == 1


# -- 2. tool lines ------------------------------------------------------------------------------------------------
class Stream:
    """A Chat with its stream scripted."""

    def __init__(self, the_look=None, **kw):
        self.out = (the_look or look()).out
        self.chat = termchat.Chat(types.SimpleNamespace(info={"port": 1}), look=the_look or look(out=self.out),
                                  read_line=lambda p="": "", **kw)
        self.chat.pending = lambda: []

    def run(self, *events):
        self.chat._events(iter(events), set(), [])
        return self.out.getvalue()


def test_the_screenshot_turn_has_no_raw_tool_lines():
    text = Stream().run({"type": "progress", "tool": "list_installed", "line": "Using list_installed ..."},
                        {"type": "tool_done", "tool": "list_installed", "ok": True},
                        {"type": "token", "text": "**Pu"}, {"type": "token", "text": "ff** (`cloud"},
                        {"type": "token", "text": "-avatar`) - Avatar, stopped"},
                        {"type": "done", "text": "**Puff** (`cloud-avatar`) - Avatar, stopped"})
    assert text == "  Looking at what's installed...\n  Puff (cloud-avatar) - Avatar, stopped\n"
    assert "list_installed" not in text and "done" not in text and "**" not in text


def test_every_concierge_tool_has_plain_words_and_any_other_is_working():
    assert set(termchat.TOOL_WORDS) == {*concierge.CORE_TOOL_NAMES, concierge.READ_PINNED, concierge.SEND_DOCUMENT,
                                      concierge.SHOW_PASSAGE, concierge.OPEN_IN_PROGRAM, concierge.SKILL_VIEW}
    for name in concierge.CORE_TOOL_NAMES:
        assert name not in termchat.tool_line({"tool": name})
    assert termchat.tool_line({"tool": "freshtable.add_shopping"}) == "Working..."
    assert termchat.tool_line({}) == "Working..."
    assert termchat.tool_line({"tool": "x", "line": "Fetching the page ..."}) == "Fetching the page ..."
    assert termchat.tool_line({"tool": "freshtable.add", "line": "Using freshtable.add ..."}) == "Working..."


def test_a_failed_tool_says_so_in_plain_words_and_never_its_name():
    text = Stream().run({"type": "progress", "tool": "read_app", "line": "Using read_app ..."},
                        {"type": "tool_done", "tool": "read_app", "ok": False},
                        {"type": "progress", "tool": "fresh.add", "line": "Using fresh.add ..."},
                        {"type": "tool_done", "tool": "fresh.add", "ok": False},
                        {"type": "token", "text": "Sorry."}, {"type": "done", "text": "Sorry."})
    assert text.splitlines() == ["  Reading an app's page...", "  That did not work: reading an app's page.",
                                 "  Working...", "  That did not work.", "  Sorry."]


def test_her_no_ends_a_turn_with_its_own_words_not_with_did_not_work():
    text = Stream().run({"type": "progress", "tool": "ask_toolsmith", "line": "Using ask_toolsmith ..."},
                        {"type": "tool_done", "tool": "ask_toolsmith", "ok": False},
                        {"type": "done", "text": "You said **No**, so nothing was done."})
    assert "did not work" not in text and "  You said No, so nothing was done.\n" in text


def test_the_final_text_is_drawn_the_same_and_never_said_twice():
    assert Stream().run({"type": "done", "text": "*Only* at the end"}) == "  Only at the end\n"
    text = Stream().run({"type": "token", "text": "One."},
                        {"type": "progress", "tool": "read_app", "line": "Using read_app ..."},
                        {"type": "tool_done", "tool": "read_app", "ok": True},
                        {"type": "token", "text": "Two."}, {"type": "done", "text": "One.\n\nTwo."})
    assert text.count("One.") == 1 and text.count("Two.") == 1
    text = Stream().run({"type": "token", "text": "Hi."}, {"type": "done", "text": "Hi.\n\nWhat **next**?"})
    assert text == "  Hi.\n  What next?\n"


def test_a_cut_stream_still_shows_the_words_it_got():
    def events():
        yield {"type": "token", "text": "Half an **ans"}
        raise OSError("cut")
    s = Stream()
    with pytest.raises(OSError):
        s.chat._events(events(), set(), [])
    assert s.out.getvalue() == "  Half an ans\n"


def test_turn_steps_lose_their_markers_but_a_command_stays_as_it_came():
    s = Stream()
    steps = [{"id": "a", "title": "**Water** the plants", "why": "You *asked*.", "kind": "run_command",
              "text": "echo **hi** _there_"}]
    s.chat.api.call = lambda method, path, body=None, timeout=0: (200, steps if "checklist" in path else [])
    s.chat.cmd_turn()
    text = s.out.getvalue()
    assert "1  Water the plants" in text and "   You asked." in text and "echo **hi** _there_" in text


# -- 3. the command search ----------------------------------------------------------------------------------------
def chat_out(the_look=None):
    return Stream(the_look).chat


def test_pass_finds_what_the_mockup_shows():
    chat = chat_out()
    chat.command("/pass")
    lines = chat.look.out.getvalue().splitlines()
    assert lines[0] == '  3 commands match "pass":'
    assert [ln.split()[0] for ln in lines[1:]] == ["/vault", "/unlock", "/lock"]
    assert all(ln.rstrip().endswith("Core") for ln in lines[1:])


def test_one_match_still_says_what_it_found_and_does_not_run_it():
    chat = chat_out()
    chat.command("/api")
    lines = chat.look.out.getvalue().splitlines()
    assert lines[0] == '  1 command matches "api":'
    assert lines[1].split()[0] == "/keys" and "API keys and Toolsmith's cloud model" in lines[1]
    assert lines[1].rstrip().endswith("Core") and len(lines) == 2


def test_the_search_reads_name_words_and_what_it_does_and_every_command_has_words():
    for name, *_ in termchat.COMMANDS:
        assert termchat.COMMAND_WORDS[name].split(), name
        assert name in [r["name"] for r in termchat.find_commands(name)[1]]
    for name in termchat.NOT_YET:
        assert termchat.COMMAND_WORDS[name].split() and termchat.NOT_YET_WHAT[name]
    assert [r["name"] for r in termchat.find_commands("hidden")[1]] == ["unlock"]       # what it does
    assert "brain" in [r["name"] for r in termchat.find_commands("model")[1]]           # its words
    assert termchat.find_commands("bypass")[1] == [] and termchat.find_commands("ass")[1] == []
    assert [r["name"] for r in termchat.find_commands("vault pin")[1]] == ["vault", "unlock", "lock"]


def test_a_word_with_no_match_is_never_said_back_and_she_can_ask_the_bot():
    for typed in ("/zz1234", "/1234", "/unlock1234", "/help 1234", "/qwzx secret"):
        chat = chat_out()
        chat.command(typed)
        text = chat.look.out.getvalue()
        assert "There is no such command." in text and "To ask your bot instead" in text, typed
        for bad in ("1234", "secret", "zz", "qwzx"):
            assert bad not in text, typed
    table = json.dumps(termchat.all_commands())
    assert not re.search(r"\d", table)                 # so a search hit can never say back a PIN


def test_slash_alone_is_the_list_and_help_with_a_word_is_the_search():
    chat = chat_out()
    chat.command("/")
    assert "/vault" in chat.look.out.getvalue() and "/quit" in chat.look.out.getvalue()
    chat = chat_out()
    chat.command("/help vault")
    assert 'match "vault":' in chat.look.out.getvalue()


def test_puffpai_help_and_commands_need_no_core(state, capsys):
    assert cli.main(["help", "pass"]) == 0
    assert '3 commands match "pass":' in capsys.readouterr().out
    assert cli.main(["help", "no1such2word"]) == 1
    assert "no1such2word" not in capsys.readouterr().out
    assert cli.main(["help"]) == 0
    assert "/build" in capsys.readouterr().out
    assert cli.main(["commands"]) == 0
    assert "/build" in capsys.readouterr().out
    assert cli.main(["commands", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["name"] for r in rows} == {c[0] for c in termchat.COMMANDS} | set(termchat.NOT_YET)
    assert all(set(r) == {"name", "args", "what", "words", "here"} and r["what"] and r["words"] for r in rows)
    assert {r["name"] for r in rows if not r["here"]} == set(termchat.NOT_YET)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE", str(tmp_path))
    return tmp_path


# -- 4. the header, the card, /watch ------------------------------------------------------------------------------
class Api:
    def __init__(self, **routes):
        self.info = {"port": 1234}
        self.routes = {"/v1/settings": {"bot": {"name": "SpaceBot"}},
                       "/v1/model": {"settings": {"model": "qwen3:8b"}, "state": {"state": "ready"}},
                       "/v1/vault/status": {"state": "open"}, "/v1/bridge": {"state": "closed"},
                       "/v1/toolsmith": {"builds": []}, "/v1/checklist": [], "/v1/consent?status=pending": []} | routes

    def call(self, method, path, body=None, timeout=0):
        got = self.routes.get(path)
        return (200, got) if got is not None else (404, {})


def api_chat(api, the_look=None, keys=()):
    keys = list(keys)
    return termchat.Chat(api, look=the_look or look(), read_line=lambda p="": keys.pop(0) if keys else "",
                         sleep=lambda s: None)


def plan_card(build="b-1"):
    return {"consent_id": "c_7f3a00", "title": 'Plan for "track the ISS"', "lines": ["1  Adopt  open-notify-tracker"],
            "card": {"covers": {"build": build, "plan_sha": "x"}}, "card_sha": "s"}


def test_the_header_matches_the_mockup():
    api = Api(**{"/v1/consent?status=pending": [plan_card()]})
    line = api_chat(api, look(out=io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))).status_line()
    assert line == " SpaceBot · brain qwen3:8b · vault open · Bridge closed · 1 build waiting on you"
    painted = api_chat(api, color()).status_line()
    assert "Bridge \x1b[32mclosed\x1b[0m" in painted and "\x1b[33m1 build waiting on you\x1b[0m" in painted


def test_the_header_counts_builds_waiting_going_and_other_turn_steps():
    def line(**routes):
        return api_chat(Api(**routes), look(out=io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))).status_line()
    assert line().endswith("Bridge closed · no builds waiting")
    assert line(**{"/v1/checklist": [{"id": "a"}]}).endswith("no builds waiting · Your turn 1")
    builds = {"builds": [{"id": "a", "jobs": [{"status": "waiting_you"}]}, {"id": "b", "jobs": [{"status": "waiting_vault"}]},
                         {"id": "c", "jobs": [{"status": "building: x"}]}, {"id": "d", "jobs": [{"status": "waiting_yes"}]},
                         {"id": "e", "jobs": [{"status": "working"}]}]}
    assert line(**{"/v1/toolsmith": builds}).endswith("2 builds waiting on you · 1 build going")


def test_the_plan_card_has_its_title_and_id_in_the_top_line_and_the_keys_under_it():
    out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\n")
    prompts = []

    def read_line(prompt=""):
        prompts.append(prompt)
        return ""
    chat = termchat.Chat(Api(), look=look(out=out), read_line=read_line)
    assert chat.answer(plan_card(), later=True) == "later"
    out.flush()
    lines = out.buffer.getvalue().decode("utf-8").splitlines()
    assert re.fullmatch(r'  ┌ Plan for "track the ISS" +card 7f3a00 ┐', lines[0])
    assert lines[1].startswith("  │ 1  Adopt")
    assert lines[-3].startswith("  └─") and lines[-3].endswith("┘")
    assert len({len(ln) for ln in lines[:-2]}) == 1      # every line of the box the same width
    assert lines[-2] == "  Build this plan?  [y] yes  [n] no  [?] what does this mean  [Enter] later"
    assert prompts == ["> "]


def test_a_long_title_goes_on_inside_the_box():
    out = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="\n")
    chat = termchat.Chat(Api(), look=look(out=out))
    chat.box("word " * 30, ["a line"], "card 7f3a00")
    out.flush()
    lines = out.buffer.getvalue().decode("ascii").splitlines()
    assert lines[0].startswith("  + word") and lines[0].endswith("card 7f3a00 +")
    assert len({len(ln) for ln in lines}) == 1


def _build(status, run=None):
    return {"goal": "track the ISS", "jobs": [{"name": "Tracker", "status": status, "run": run or {}}]}


def _watch(the_look, *states):
    states = list(states)

    class Seq(Api):
        def call(self, method, path, body=None, timeout=0):
            if path.startswith("/v1/toolsmith/builds/"):
                return 200, states.pop(0) if len(states) > 1 else states[0]
            return super().call(method, path, body, timeout)
    chat = api_chat(Seq(), the_look)
    assert chat.watch("b-1") == 0
    return the_look.out.getvalue()


def test_watch_marks_each_step_done_on_a_live_console():
    text = _watch(look(tty=True, vt=True), _build("building: tracker", {"now": "fetched it"}),
                  _build("building: tracker", {"now": "Drawbridge check: pass"}), _build("working"))
    shown = text.replace("\r\x1b[K", "\n")
    lines = [ln for ln in re.sub(r"\x1b\[[0-9;]*m", "", shown).splitlines() if ln.strip()]
    assert 'Build b-1 "track the ISS"' in lines[0]
    assert "  ✓ Tracker: trying tracker (fetched it)" in lines
    assert "  ✓ Tracker: trying tracker (Drawbridge check: pass)" in lines
    assert "  ✓ Tracker: working" in lines


def test_watch_gives_no_check_mark_to_a_step_that_failed_or_an_app_given_up_on():
    text = _watch(look(tty=True, vt=True), _build("building: first", {"now": "starting"}),
                  _build("building: second", {"now": "starting"}), _build("not_yet"))
    lines = re.sub(r"\x1b\[[0-9;]*m", "", text.replace("\r\x1b[K", "\n")).splitlines()
    assert "  - Tracker: trying first (starting)" in lines
    assert "  - Tracker: not working yet" in lines
    assert not any("✓" in ln for ln in lines)


def test_watch_marks_are_ascii_where_the_console_cant_show_them_and_a_pipe_gets_plain_lines():
    out = io.TextIOWrapper(io.BytesIO(), encoding="ascii", newline="\n")
    the_look = look(tty=True, vt=True, out=out)
    assert the_look.check == "ok" and the_look.doing == ".."
    utf8 = look(out=io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert utf8.check == "✓"
    text = _watch(look(), _build("building: x", {"now": "go"}), _build("working"))
    assert "\r" not in text and "Tracker: trying x (go)" in text


# -- 5. /window ---------------------------------------------------------------------------------------------------
def test_window_says_the_line_it_gets_and_the_plain_words_reach_it(monkeypatch):
    seen = []
    fake = types.ModuleType("puffpai_core.window")
    fake.open_window = lambda port: (seen.append(port), (True, "Opened the PuffPAI window."))[1]
    monkeypatch.setitem(sys.modules, "puffpai_core.window", fake)
    chat = api_chat(Api())
    assert chat.command("/window") == 0
    assert seen == [1234] and "Opened the PuffPAI window." in chat.look.out.getvalue()
    keys = ["open the window", "Show me my window!", "open window", "/quit"]
    chat = api_chat(Api(), keys=keys)
    chat.turn = lambda text: pytest.fail(f"went to the brain: {text}")
    assert chat.run() == 0
    assert seen == [1234] * 4
    fake.open_window = lambda port: (False, "It did not open.")
    chat = api_chat(Api())
    assert chat.cmd_window() == 1 and "It did not open." in chat.look.out.getvalue()


def test_other_words_about_a_window_are_chat(monkeypatch):
    fake = types.ModuleType("puffpai_core.window")
    fake.open_window = lambda port: pytest.fail("opened")
    monkeypatch.setitem(sys.modules, "puffpai_core.window", fake)
    said = []
    chat = api_chat(Api(), keys=["open the window please", "what is a window", "/quit"])
    chat.turn = said.append
    chat.run()
    assert said == ["open the window please", "what is a window"]


def test_window_missing_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "puffpai_core.window", None)     # the import raises ImportError
    chat = api_chat(Api())
    assert chat.command("/window") == 1
    assert termchat.NO_WINDOW in chat.look.out.getvalue()
