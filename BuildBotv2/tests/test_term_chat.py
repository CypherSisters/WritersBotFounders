"""D86 (lane term-chat): ``puffpai`` with no words is the bot's chat in a terminal, a thin client of the running
Core. A real Core runs in this process on free ports (never 8420-8422) with a temp bot folder; the brain is a
stub model and Toolsmith a stub host over the real build store, so the Yes cards are the real consent store's.
Three unrelated goals go from chat to a plan card drawn in text; y answers it with that card's card_sha; a
changed card is refused; a floor card refuses a single key and takes the PIN; the PIN never shows in the
output, the bot's files or the logs; NO_COLOR has no escape codes; ASCII boxes where Unicode can't show."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from conftest import REPO
from zone2_doubles import GateDouble
from puffpai_core import cli, termchat
from puffpai_core.zone3.toolsmith_host import store as store_mod

PIN = "73914682"
GOALS = ["a recipe box for my family", "invoices for a small shop", "a birdwatching log"]


def _free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in (8420, 8421, 8422):
            return port


# -- stubs: the brain's model and Toolsmith's host ----------------------------------------------------------
class StubModel:
    """The brain's model: "add <words>" calls ask_toolsmith with the words; a tool result is said back; any
    other text gets one plain line."""

    def __init__(self):
        self.seen: list[str] = []

    def is_available(self):
        return True

    def models(self):
        return [{"name": "fake:1b", "size_bytes": 1, "context": None}]

    def chat(self, messages, *, model, tools=None, think=False, options=None, on_token=None):
        last = messages[-1]
        self.seen.append(json.dumps(messages, ensure_ascii=False))
        if last.get("role") == "tool":
            text = "Toolsmith says: " + str(last.get("content") or "")[:200]
        else:
            words = str(last.get("content") or "")
            if words.lower().startswith("add "):
                return {"text": "", "tool_calls": [{"id": "c1", "name": "ask_toolsmith",
                                                    "arguments": {"job": words[4:]}}]}
            text = "Hello from the test brain."
        if on_token:
            on_token(text)
        return {"text": text, "tool_calls": []}

    def chat_stream(self, messages, *, model, think=False, options=None):
        yield self.chat(messages, model=model)["text"]


class Host:
    """Toolsmith's host, stubbed over the real build store: each job gets one app to try, and the plan card is
    a real card in the Core's consent store (``package``, bound to the plan sha), as the real host raises it."""

    def __init__(self, state, consent, vault_open=lambda: True):
        self.store = store_mod.Builds(state)
        self.consent, self.vault_open = consent, vault_open
        self.live, self.started, self.unlock = set(), [], False
        self.offers, self.used = [], []

    def settings(self):
        return {"where": "local"}

    def status(self):
        return {"where": "local", "builds": [{"id": b["id"], "goal": b["goal"], "created_at": b["created_at"],
                                              "jobs": [{"id": j["id"], "name": j["name"],
                                                        "status": store_mod.job_status(b, j)} for j in b["jobs"]]}
                                             for b in self.store.all()]}

    def draft(self, goal, jobs=None):
        b = self.store.create(goal, jobs or [{"name": goal[:40], "does": goal}], {"where": "local"})
        self.store.change(b["id"], lambda x: x.update(planning=False))
        time.sleep(1.01)                         # build ids are made to the second
        return self.view(b["id"])

    def plan(self, goal, jobs=None):
        return self.plan_build(self.draft(goal, jobs)["id"])

    def use_offers(self, bid, cid, use, by=""):
        self.used.append((bid, cid, list(use or [])))
        return list(use or [])

    def plan_build(self, bid):
        b = self.store.read(bid)
        sha = hashlib.sha256(bid.encode()).hexdigest()
        card = {"title": f'Plan for "{b["goal"][:60]}"',
                "sentence": "When you say Yes, Toolsmith tries these apps in a folder of their own.",
                "lines": [f"{j['name']}: try example-app (runs as you)" for j in b["jobs_in"]],
                "offers": list(self.offers)}
        got = self.consent.ask("package", store_mod.bridge_target(bid),
                               {"build": bid, "plan_sha": sha, "card": card, "install_hosts": ["github.com"]},
                               bound={"plan_sha": sha})

        def fn(x):
            x["jobs"] = [{"id": f"{bid}-{n}", "n": n, "name": j["name"], "does": j["does"],
                          "plan": {"candidates": [{"ref": "example/app", "name": "example-app"}]},
                          "run": {"state": "planned", "line": "", "trying": None, "tried": []}}
                         for n, j in enumerate(x["jobs_in"])]
            x.update(plan_sha=sha, planning=False, consent={"id": got["consent_id"], "said": ""}, card=card)
        self.store.change(bid, fn)
        return self.view(bid)

    def view(self, bid):
        b = self.store.read(bid)
        if b is None:
            raise KeyError(bid)
        for j in b["jobs"]:
            j["status"] = store_mod.job_status(b, j)
        b["keys"] = {"cloud": self.unlock, "leased": False, "unlock": bool(self.unlock and not self.vault_open())}
        return b

    def running(self, bid):
        return bid in self.live

    def start(self, bid, cid):
        rec = self.consent.get(cid) or {}
        if rec.get("status") != "granted":
            raise PermissionError("No Yes for this plan.")

        def fn(x):
            x["yes"] = {"given": True, "consent_id": cid}
            for j in x["jobs"]:
                j["run"].update(state="building", trying="example-app", now="starting the app")
        self.store.change(bid, fn)
        self.live.add(bid)
        if bid not in self.started:
            self.started.append(bid)
        return self.view(bid)

    def replace(self, old, new):
        cid = self.store.end_replaced(old, new)
        if cid:
            self.consent.expire(cid, store_mod.REPLACED_LINE)
        return cid is not None

    def finish(self, bid):
        def fn(x):
            for j in x["jobs"]:
                j["run"].update(state="working", trying=None, now="", line="Works: it answered its sample ask.")
        self.store.change(bid, fn)
        self.live.discard(bid)

    def stop(self, *a, **k):
        for bid in list(self.live):
            self.finish(bid)


@pytest.fixture
def live(state):
    """A real Core on free ports, its terminal key in ``settings\\terminal.json``, a stub model and host."""
    with _live(state) as got:
        yield got


@contextlib.contextmanager
def _live(state):
    from puffpai_core.api.app import create_app, make_servers, run_servers, start_core, stop_core
    from puffpai_core.api.groups import chat as chat_group
    from puffpai_core.zone2 import settings as model_settings
    port, pages = _free_port(), _free_port()
    app = create_app(state, port=port, pages_port=pages)
    core = app.extensions["puffpai"]
    core.app, core.proxy_port = app, 0
    from puffpai_core.zone1 import archivist

    def vault_open():
        a = archivist.for_state(state, core.bus)
        return a.vault.status().get("state") == "open"
    host = Host(state, core.consent, vault_open)
    app.extensions["puffpai_toolsmith"] = host           # the Core's Toolsmith host is this stub
    servers = make_servers(app, port, pages)
    terminal = start_core(app)
    stop = threading.Event()
    t = threading.Thread(target=run_servers, args=(servers,), kwargs={"stop": stop}, daemon=True)
    t.start()
    model_settings.save(state, address="http://127.0.0.1:9", model="fake:1b")
    with app.app_context():
        brain = chat_group.brain()
    brain._runtime = StubModel()
    brain._gate = GateDouble().gate
    info = json.loads((state / "settings" / "terminal.json").read_text(encoding="utf-8"))
    assert info["port"] == port and port not in (8420, 8421, 8422)
    try:
        yield types.SimpleNamespace(app=app, core=core, host=host, info=info, state=state, brain=brain)
    finally:
        host.stop()
        stop.set()
        t.join(timeout=15)
        stop_core(core, terminal)
        core.steward.stop_all(remember=False)


class Spy:
    """The Api, with every call written down (a PIN or vault secret only as "<pin>")."""

    def __init__(self, api, tamper=None):
        self.api, self.calls, self.tamper = api, [], tamper

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        shown = None if body is None else {k: ("<pin>" if k in ("pin", "secret") else v) for k, v in body.items()}
        self.calls.append((method, path, shown))
        st, got = self.api.call(method, path, body, timeout)
        if self.tamper is not None:
            got = self.tamper(method, path, got)
        return st, got

    def stream(self, path, body):
        self.calls.append(("POST", path, dict(body)))
        return self.api.stream(path, body)

    def answers(self):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith("/answer")]


class Keys:
    """Her keys, typed one by one; no more keys is Ctrl+D (EOF)."""

    def __init__(self, *keys):
        self.keys, self.prompts = list(keys), []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        if not self.keys:
            raise EOFError
        return self.keys.pop(0)


def _chat(live, keys=(), hidden=(), tamper=None, env=None, out=None, tty=False, vt=False):
    out = out or io.StringIO()
    hidden_keys = Keys(*hidden)
    look = termchat.Look(out, env=env if env is not None else {}, tty=lambda s: tty, vt_on=lambda s: vt)
    opened = []
    chat = termchat.Chat(Spy(termchat.Api(live.info), tamper), read_line=Keys(*keys), read_hidden=hidden_keys,
                         open_url=opened.append, sleep=lambda s: None, look=look)
    chat.opened, chat.out, chat.hidden = opened, out, hidden_keys
    return chat


def _setup_vault(live):
    st, body = termchat.Api(live.info).call("POST", "/v1/vault/setup", {"pin": PIN})
    assert st == 201, body


def _pending(live):
    return [c for c in live.core.consent.list("pending")]


# -- 1. three goals: chat -> plan card in text -> y with its card_sha ------------------------------------------
@pytest.mark.parametrize("goal", GOALS)
def test_a_goal_in_chat_shows_its_plan_card_and_y_answers_it_with_its_card_sha(live, goal):
    chat = _chat(live, keys=["y"])
    assert chat.turn(f"add {goal}") is True
    text = chat.out.getvalue()
    b = live.host.store.all()[-1]
    assert goal in b["goal"]
    cid = b["consent"]["id"]
    rec = live.core.consent.get(cid)
    assert rec["status"] == "granted" and rec["factor"] == "tap"
    sent = chat.api.answers()
    assert len(sent) == 1 and sent[0][2]["card_sha"] == rec["card_sha"] and sent[0][2]["answer"] == "yes"
    assert "pin" not in sent[0][2]
    # The card as the Desktop shows it: the Core's own title and lines, its short id, the privacy line.
    assert f'Plan for "{goal}' in text and f"card {termchat.short(cid)}" in text
    assert "When you say Yes, Toolsmith tries these apps" in text and termchat.PRIVACY in text
    assert "Yes recorded" in text and "Toolsmith says:" in text
    assert live.host.started == [b["id"]]               # the chat turn started it (no second start from here)
    assert not any(c[1].endswith("/start") for c in chat.api.calls)
    # The chat went through the Desktop's own route, in its conversation.
    assert ("POST", "/v1/chat/stream", {"text": f"add {goal}"}) in chat.api.calls


def test_build_verb_plans_shows_the_card_and_y_starts_the_build(live):
    chat = _chat(live, keys=["y"])
    assert chat.command("/build " + GOALS[1]) == "yes"
    b = live.host.store.all()[-1]
    sent = chat.api.answers()
    assert sent[0][2]["card_sha"] == live.core.consent.get(b["consent"]["id"])["card_sha"]
    assert any(c[1] == f"/v1/toolsmith/builds/{b['id']}/start" for c in chat.api.calls)   # the Desktop's afterPlanYes
    assert live.host.started == [b["id"]]
    assert "Building. /watch follows it here" in chat.out.getvalue()


def test_no_says_no_and_nothing_starts(live):
    live.host.plan(GOALS[2])
    chat = _chat(live)
    assert chat.command("/no") == "no"
    rec = live.core.consent.list()[-1]
    assert rec["status"] == "declined" and live.host.started == []
    assert "No recorded" in chat.out.getvalue()


def test_e_ticks_an_offer_and_the_ticks_go_before_the_yes(live):
    live.host.offers = [{"job": 0, "name": "FreshTable", "package": "freshtable",
                         "line": "Invoices for a small shop: FreshTable may cover this. Tick to use it instead of searching."}]
    b = live.host.plan(GOALS[1])
    chat = _chat(live, keys=["e", "1", "y"])
    assert chat.command("/plan") == "yes"
    out = chat.out.getvalue()
    assert " 1 [ ] Invoices for a small shop: FreshTable may cover this." in out and "Ticked: Invoices" in out
    assert live.host.used == [(b["id"], b["consent"]["id"], [0])]
    paths = [c[1] for c in chat.api.calls]
    assert paths.index(f"/v1/toolsmith/builds/{b['id']}/use") < paths.index(chat.api.answers()[0][1])
    assert live.core.consent.get(b["consent"]["id"])["status"] == "granted"


# -- 2. a changed card is refused -----------------------------------------------------------------------------
def test_a_card_that_changed_after_she_saw_it_is_refused_and_shown_again(live):
    live.host.plan(GOALS[0])
    cid = _pending(live)[0]["consent_id"]
    stale = {"n": 0}

    def tamper(method, path, got):                    # the first listing is the card as it was before it changed
        if path.startswith("/v1/consent?") and isinstance(got, list) and stale["n"] == 0:
            stale["n"] += 1
            return [dict(c, card_sha="0" * 64) if c.get("consent_id") == cid else c for c in got]
        return got
    chat = _chat(live, keys=["y", "n"], tamper=tamper)
    chat.command("/yes " + termchat.short(cid))
    sent = chat.api.answers()
    assert sent[0][2]["card_sha"] == "0" * 64             # her y named the card she saw...
    assert termchat.CHANGED_AGAIN in chat.out.getvalue()  # ...which the Core refused (D57); she sees it fresh
    assert sent[1][2]["answer"] == "no" and sent[1][2]["card_sha"] == live.core.consent.get(cid)["card_sha"]
    assert live.core.consent.get(cid)["status"] == "declined"


# -- 3. a floor card: never one key, the PIN ------------------------------------------------------------------
def test_a_floor_card_refuses_a_single_key_and_takes_the_pin(live):
    _setup_vault(live)
    card = live.core.consent.ask("device_change", "porch-light", {"hosts": ["light.local"]})
    cid = card["consent_id"]
    assert live.core.consent.get(cid)["floor"] is True
    chat = _chat(live, keys=["y", "p", "p"], hidden=["11112222", PIN])
    assert chat.command("/yes") == "yes"
    out = chat.out.getvalue()
    assert termchat.NEEDS_PIN in out                       # y alone was refused here, nothing was sent for it
    sent = chat.api.answers()
    assert [s[2]["factor"] for s in sent] == ["pin", "pin"] and all(s[2]["pin"] == "<pin>" for s in sent)
    assert "That PIN is not right." in out                 # the wrong PIN first: the Core said no
    rec = live.core.consent.get(cid)
    assert rec["status"] == "granted" and rec["factor"] == "pin"
    assert PIN not in out and "11112222" not in out


# -- 4. D68: a plan Yes with the vault locked asks for the PIN in place --------------------------------------
def test_a_plan_yes_with_a_locked_vault_asks_for_the_pin_and_opens_it_first(live):
    _setup_vault(live)
    api = termchat.Api(live.info)
    assert api.call("POST", "/v1/vault/lock", {})[0] == 200
    live.host.unlock = True                                # this build needs its cloud key (D68)
    live.host.plan(GOALS[2])
    chat = _chat(live, keys=["y"], hidden=[PIN])
    assert chat.command("/plan") == "yes"
    out = chat.out.getvalue()
    assert termchat.UNLOCK_FOR_YES in out and "Your vault is open." in out
    paths = [c[1] for c in chat.api.calls]
    assert paths.index("/v1/vault/unlock") < paths.index(chat.api.answers()[0][1])
    sent = chat.api.answers()[0][2]
    assert sent["factor"] == "tap" and "pin" not in sent  # a tap card never carries her PIN
    assert api.call("GET", "/v1/vault/status")[1]["state"] == "open"
    assert PIN not in out


# -- 5. the PIN never shows: output, history, files, logs, errors ---------------------------------------------
def test_the_pin_never_shows_in_the_output_history_files_or_logs(live, caplog):
    caplog.set_level(logging.DEBUG)
    _setup_vault(live)
    termchat.Api(live.info).call("POST", "/v1/vault/lock", {})
    keys = ["/vault", "/unlock " + PIN, "/unlock" + PIN, "/unlock", "/lock", "hello", "/unlock", "/quit"]
    chat = _chat(live, keys=keys, hidden=[PIN, PIN])

    real = chat.api.api.call

    def gone(method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        if path == "/v1/vault/unlock" and chat.hidden.keys == []:
            import urllib.error
            raise urllib.error.URLError("connection refused")      # BuildBot stops while she unlocks
        return real(method, path, body, timeout)
    chat.api.api.call = gone
    assert chat.run() == 1                                       # the Core went away: said plainly, then out
    out = chat.out.getvalue()
    assert termchat.PIN_ARG in out and termchat.GONE in out and "Your vault is open." in out
    assert "There is no such command. Did you mean /unlock?" in out      # "/unlock<PIN>" is not said back
    # "/unlock <PIN>" typed on one line is refused and sent nowhere; the chat never got any PIN.
    assert not any(PIN in json.dumps(c, ensure_ascii=False) for c in chat.api.calls)
    assert all(PIN not in s for s in live.brain._runtime.seen)
    assert PIN not in out and PIN not in caplog.text
    for path in Path(live.state).rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert PIN.encode() not in data and PIN.encode("utf-16-le") not in data, path


# -- 6. the look: NO_COLOR, color with words, ASCII boxes -------------------------------------------------------
def test_no_color_means_no_escape_codes_and_color_always_comes_with_words(live):
    live.host.plan(GOALS[0])
    chat = _chat(live, keys=["", "/help", "/bridge"], env={"NO_COLOR": "1"}, tty=True, vt=True)
    assert chat.look.color is False
    chat.run()
    assert "\x1b" not in chat.out.getvalue()
    colored = _chat(live, keys=["/bridge"], env={}, tty=True, vt=True)
    colored.run()
    text = colored.out.getvalue()
    assert "\x1b[" in text                                  # color is on where it may be...
    assert "Bridge closed" in text or "Bridge \x1b" in text  # ...and the Bridge is said in words too
    assert "The Bridge is closed." in text


def test_text_from_the_core_never_writes_escape_codes_or_bidi_marks(live):
    """An app's name or the brain's words are shown as text, as the Desktop shows them (textContent)."""
    live.core.consent.request({"kind": "tool_call", "target": "recipe-box.add"},
                              {"title": "Add to \x1b[2J\x1b]0;owned\x07Recipe Box\u202e?", "what": "x\x9bline",
                               "package": "recipe-box", "tool": "add", "arguments": ["a"]}, False)
    chat = _chat(live, keys=["n"])
    chat.command("/yes")
    text = chat.out.getvalue()
    assert "Recipe Box" in text and "No recorded" in text
    for bad in ("\x1b", "\x07", "\x9b", "\u202e"):
        assert bad not in text
    assert termchat.clean("a\tb\x1b[31mc\nd") == "a    b[31mc\nd"
    for url in ("file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "http://x y", "https://",
                "http://127.0.0.1:5/\x1b[2J"):
        assert chat._open(url) is False
    assert chat.opened == []
    assert chat._open("http://127.0.0.1:51734/recipes") is True and chat.opened == ["http://127.0.0.1:51734/recipes"]


def test_ascii_boxes_where_unicode_cant_show(live):
    live.host.plan(GOALS[1])
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii", errors="strict", newline="\n")
    chat = _chat(live, keys=[""], out=out)
    assert chat.look.unicode is False
    chat.command("/plan")
    out.flush()
    text = raw.getvalue().decode("ascii")                   # every byte is ASCII
    assert "+---" in text and "+ Plan for" in text
    if os.name == "nt":                                     # the old console: no escape codes, so no box lines
        old = termchat.Look(io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), env={}, tty=lambda s: True,
                            vt_on=lambda s: False)
        assert old.unicode is False and old.color is False
    new = termchat.Look(io.TextIOWrapper(io.BytesIO(), encoding="utf-8"), env={}, tty=lambda s: True,
                        vt_on=lambda s: True)
    assert new.unicode is True and new.g["tl"] == "\u250c"


# -- 7. the start, /help, unknown commands, the Core not running -----------------------------------------------
def test_the_status_bar_then_type_to_chat(live):
    live.host.plan(GOALS[0])                                # a plan card waits: one build waits on her
    chat = _chat(live, keys=["/quit"])
    assert chat.run() == 0
    first, second = chat.out.getvalue().splitlines()[:2]
    from puffpai_core import bot
    assert first.startswith(" " + bot.read(live.state)["name"]), first
    for part in ("brain fake:1b", "vault not set up yet", "Bridge closed", "1 build waiting on you"):
        assert part in first, first
    assert second.strip() == termchat.TYPE_TO_CHAT
    assert termchat.BYE in chat.out.getvalue()


def test_help_lists_every_command_and_says_what_is_not_yet(live):
    chat = _chat(live)
    chat.command("/help")
    text = chat.out.getvalue()
    for name, _args, _what in termchat.COMMANDS:
        assert f"/{name}" in text
    assert "Not yet here" in text and "/github" in text and termchat.PRIVACY in text
    chat.command("/vualt")
    assert "Did you mean /vault?" in chat.out.getvalue()
    chat.command("/github")
    assert "/github is not in the terminal yet" in chat.out.getvalue()


def test_with_no_core_running_it_says_so_and_how_to_start(state, capsys):
    assert not (state / "settings" / "terminal.json").exists()
    assert cli.main([]) == 1
    out = capsys.readouterr().out
    assert termchat.NOT_RUNNING in out
    assert "Start BuildBot.cmd" in out or out.rstrip().endswith("serve")   # the launcher, or a command that really runs
    assert "puffpai serve" not in out
    for verb in (["build", "x"], ["yes"], ["watch"], ["vault", "status"]):
        assert cli.main(verb) == 1
        assert termchat.NOT_RUNNING in capsys.readouterr().out


# -- 8. /watch: progress, cards inline, Ctrl+C leaves it running ----------------------------------------------
def test_watch_follows_a_build_shows_new_cards_and_ctrl_c_leaves_it_running(live):
    b = live.host.plan(GOALS[2])
    cid = b["consent"]["id"]
    live.core.consent.answer(cid, "yes", "tap", None, by="test", card_sha=live.core.consent.get(cid)["card_sha"])
    live.host.start(b["id"], cid)
    ticks = {"n": 0}

    def sleep(_s):
        ticks["n"] += 1
        if ticks["n"] == 1:                                 # a hold card for this build comes while she watches
            live.core.consent.request({"kind": "package", "target": "example-app"},
                                      {"title": "Let Toolsmith try this app?", "build": b["id"],
                                       "try_hold": {"name": "example-app", "url": "https://github.com/example/app",
                                                    "why": "Drawbridge could not check 2 files."}}, False)
        if ticks["n"] == 2:
            raise KeyboardInterrupt
    chat = _chat(live, keys=["n"])
    chat.sleep = sleep
    assert chat.command("/watch") == 0
    out = chat.out.getvalue()
    assert "trying example-app (starting the app)" in out
    assert "This needs your Yes:" in out and "Let Toolsmith try this app?" in out
    assert termchat.WATCH_STOPPED in out
    assert live.host.running(b["id"])                        # Ctrl+C stopped the watching, not the build

    live.host.finish(b["id"])
    chat = _chat(live)
    assert chat.command("/watch " + b["id"]) == 0
    assert "This build has ended." in chat.out.getvalue() and "working" in chat.out.getvalue()


def test_turn_lists_steps_and_cards(live):
    st, step = termchat.Api(live.info).call("POST", "/v1/checklist", {"id": "docker", "title": "Install Docker Desktop",
                                                                      "why": "A birdwatching app runs in Docker.",
                                                                      "kind": "run_command",
                                                                      "text": "winget install -e --id Docker.DockerDesktop"})
    assert st in (200, 201), step
    live.host.plan(GOALS[0])
    chat = _chat(live)
    assert chat.command("/turn") == 0
    out = chat.out.getvalue()
    assert "1  Install Docker Desktop" in out and "winget install -e --id Docker.DockerDesktop" in out
    assert "Yes card " in out and "/turn done <n>" in out
    assert chat.command("/turn skip 1") == 0 and "Skipped: Install Docker Desktop" in chat.out.getvalue()


def test_open_sends_an_app_to_her_browser_in_a_new_tab(live, monkeypatch):
    rows = [{"id": "recipe-box", "name": "Recipe Box", "status": "running", "origin": "adopted:github.com/x/y",
             "ui": {"pages": [{"id": "main", "url": "http://127.0.0.1:51734/"}]}, "health": {"ok": True}}]

    def tamper(method, path, got):
        return rows if path == "/v1/registry" else got
    chat = _chat(live, tamper=tamper)
    real = chat.api.api.call
    chat.api.api.call = lambda m, p, b=None, timeout=60: (200, {"open": "new_tab", "url": rows[0]["ui"]["pages"][0]["url"]}) \
        if p == "/v1/packages/recipe-box/open/main" else real(m, p, b, timeout)
    assert chat.command("/open recipe box") == 0
    assert chat.opened == ["http://127.0.0.1:51734/"]
    assert "in a new tab of your browser" in chat.out.getvalue()


# -- 9. reaching it: the launchers and the end of setup --------------------------------------------------------
def test_install_writes_a_chat_launcher_and_start_says_it_exists():
    text = (REPO / "install.ps1").read_text(encoding="ascii")          # ASCII only, for the old console
    block = text[text.index("$ChatLauncher = "):text.index("Set-Content -LiteralPath $ChatLauncher")]
    assert '"Chat with BuildBot.cmd"' in block
    assert '"set ""PUFFPAI_BOT=$Name"""' in block and '"set ""PUFFPAI_STATE="""' in block
    assert '""%~dp0.venv\\Scripts\\puffpai.exe"" <con >con' in block     # no words: the chat, in this window
    assert '"call ""%~f0"" --run <nul >nul"' in block                   # no "Terminate batch job" question
    assert '"if not ""%BUILDBOT_EXIT%""==""0"" pause"' in block          # a refusal stays on screen to read
    start = text[text.index("$Launcher = "):text.index("Set-Content -LiteralPath $Launcher")]
    assert ">con echo To open another chat, double-click Chat with BuildBot.cmd next to this file." in start   # D94
    assert start.index("Chat with BuildBot.cmd") < start.index("start <con >con")
    assert "double-click Chat with BuildBot.cmd" in text[text.rindex("Pause-IfAlone") - 400:]


WRITE_CHAT = r'''
$ErrorActionPreference = "Stop"
$text = Get-Content -LiteralPath $args[0] -Raw
$m = [regex]::Match($text, '(?ms)^\$ChatLauncher = .*?^Set-Content -LiteralPath \$ChatLauncher[^\r\n]*')
if (-not $m.Success) { throw "install.ps1 has no Chat launcher block" }
$RepoDir = $args[1]
$Name = $args[2]
Invoke-Expression $m.Value
'''


def _chat_launcher(tmp_path):
    r"""A clone's folder: ``Chat with BuildBot.cmd`` written by install.ps1's own block (run by PowerShell), and
    a real console ``.venv\Scripts\puffpai.exe`` running ``console_core`` (the suite's guards) on this Python."""
    from pip._vendor.distlib.scripts import ScriptMaker
    repo = tmp_path / "Build Bot"
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    maker = ScriptMaker(None, str(scripts))
    maker.executable, maker.variants = sys.executable, {""}
    maker.make("puffpai = console_core:main")
    ps1 = tmp_path / "write_chat.ps1"
    ps1.write_text(WRITE_CHAT, encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(ps1), str(REPO / "install.ps1"), str(repo), "testbot"],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    launcher = repo / "Chat with BuildBot.cmd"
    assert "testbot" in launcher.read_text(encoding="ascii")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_"))}
    env.update(LOCALAPPDATA=str(tmp_path / "lad"), PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]))
    return repo, launcher, env


CMD = os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe")


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe and PowerShell")
def test_chat_with_buildbot_cmd_says_buildbot_is_not_running_and_waits_to_be_read(tmp_path):
    import console_kit
    repo, launcher, env = _chat_launcher(tmp_path)
    got = console_kit.run([CMD, "/c", str(launcher)], "none", env, termchat.NOT_RUNNING, tmp_path / "console",
                          screen=True, cwd=str(repo), settle_s=1.5, wait_s=3.0)
    screen = got["output"]
    assert got["ready"] and got["exit"] is None, screen                # paused on her key: she can read it
    assert termchat.NOT_RUNNING in screen and "Press any key to continue" in screen, screen
    assert "Terminate batch job" not in screen, screen


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe and a real console")
def test_chat_with_buildbot_cmd_chats_with_the_running_bot_in_a_real_console(tmp_path):
    """The launcher reaches the bot it names (PUFFPAI_BOT) through its terminal key, draws the status line in a
    real console, reads her typed keys from the console (<con), and /quit leaves with no pause and no batch
    question while the bot keeps running. Not covered: a real Ctrl+C key at the prompt (the test's
    GenerateConsoleCtrlEvent does not end a pending console read the way a key press does)."""
    import console_kit
    repo, launcher, env = _chat_launcher(tmp_path)
    with _live(tmp_path / "lad" / "PuffPAI" / "testbot") as bot:
        got = console_kit.run([CMD, "/c", str(launcher)], "none", env, termchat.TYPE_TO_CHAT, tmp_path / "console",
                              screen=True, cwd=str(repo), settle_s=2.0, wait_s=20.0, min_pids=3, keys="/quit\r")
        screen = got["output"]
        assert got["ready"] and got.get("typed"), screen
        assert got["exit"] == 0, (got["exit"], screen)                 # it ended by itself, with no pause
        assert "brain fake:1b" in screen and "Bridge closed" in screen, screen
        assert termchat.BYE in screen, screen
        assert " · brain fake:1b" in screen, screen               # this console reads escape codes: Unicode
        assert "Terminate batch job" not in screen and "Press any key" not in screen, screen
        assert got["left"] == [], got
        assert bot.core.terminal_label is not None                     # the bot itself keeps running


def test_the_end_of_setup_says_how_to_chat_in_a_terminal(tmp_path, monkeypatch):
    from puffpai_core.wizard import terminal
    monkeypatch.setattr(terminal, "launcher", lambda: tmp_path / "Start BuildBot.cmd")
    assert terminal.chat_line().startswith("To chat with your bot in a terminal, run this there while BuildBot runs: ")
    assert "run puffpai" not in terminal.chat_line()
    (tmp_path / "Chat with BuildBot.cmd").write_text("@echo off\n", encoding="ascii")
    assert terminal.chat_line() == ("To chat with your bot in a terminal, double-click Chat with BuildBot.cmd "
                                    f"in {tmp_path}.")
    import inspect
    from puffpai_core.wizard import engine
    assert "chat_line()" in inspect.getsource(engine.DoneStep)       # one Done step, both renderers (D36(f))


# -- fix round (verifier) ---------------------------------------------------------------------------------------
def test_watch_vault_prompt_never_takes_echoes_or_sends_digits_typed_in_view(live):
    """/watch on a build waiting for the vault: the visible prompt takes only u or Enter. A PIN typed there in
    view is not used, not printed back, and not sent; the PIN is only ever read hidden (D86(e))."""
    _setup_vault(live)
    api = termchat.Api(live.info)
    assert api.call("POST", "/v1/vault/lock", {})[0] == 200
    live.host.unlock = True                                # the build needs its cloud key and the vault is locked
    b = live.host.plan(GOALS[0])
    cid = b["consent"]["id"]
    live.core.consent.answer(cid, "yes", "tap", None, by="test", card_sha=live.core.consent.get(cid)["card_sha"])
    live.host.start(b["id"], cid)
    chat = _chat(live, keys=[PIN])
    assert "Press u, then type your PIN; it stays hidden." in termchat.UNLOCK_WAITING
    before = len(chat.api.calls)
    assert chat.command("/watch") == 0
    out = chat.out.getvalue()
    assert termchat.UNLOCK_WAITING in out and termchat.ONLY_KEYS in out
    assert PIN not in out and chat.hidden.prompts == []    # no hidden read: nothing was taken as a PIN
    after = chat.api.calls[before:]
    assert not any(PIN in json.dumps(c) for c in after)
    assert not any(c[1] == "/v1/vault/unlock" for c in after)
    assert api.call("GET", "/v1/vault/status")[1]["state"] == "locked"


def test_a_core_that_dies_mid_stream_gets_the_plain_line_not_a_traceback(live, monkeypatch):
    import http.client

    def cut(path, body):
        def events():
            yield {"type": "token", "text": "Half an ans"}
            raise http.client.IncompleteRead(b"x", 10)
        return 200, None, events()
    chat = _chat(live, keys=["hello"])
    chat.api.stream = cut
    assert chat.run() == 1
    assert termchat.GONE in chat.out.getvalue()
    # main() and a shell verb catch it too.
    monkeypatch.setattr(termchat.Chat, "run", lambda self: (_ for _ in ()).throw(http.client.HTTPException("cut")))
    out = io.StringIO()
    assert termchat.main(out=out) == 1 and termchat.GONE in out.getvalue()
    monkeypatch.setattr(termchat.Chat, "cmd_apps", lambda self, rest="": (_ for _ in ()).throw(
        http.client.RemoteDisconnected("gone")))
    out = io.StringIO()
    assert termchat.run_verb("cmd_apps", "", out=out) == 1 and termchat.GONE in out.getvalue()


def test_odd_reply_shapes_get_a_plain_line_and_help_says_never_type_the_pin_after_a_command(live):
    rows = [{"id": "bird-log", "name": "Bird Log", "status": "running", "health": "fine"}]
    steps = [{"title": "A step with no id", "kind": "confirm"}]

    def tamper(method, path, got):
        return rows if path == "/v1/registry" else steps if path == "/v1/checklist" else got
    chat = _chat(live, tamper=tamper)
    assert chat.command("/apps") == 0 and "Bird Log (bird-log): running, not checked yet" in chat.out.getvalue()
    assert chat.command("/turn done 1") == 1
    assert "That step can't be answered from here." in chat.out.getvalue()
    assert not any(c[0] == "POST" and "/v1/checklist/" in c[1] for c in chat.api.calls)
    chat.command("/help")
    chat.command("/vault")
    assert chat.out.getvalue().count(termchat.PIN_NEVER) == 2
