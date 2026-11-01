"""D133: ``puffpai vault recover`` and ``/vault recover``, on v1's behaviour and ``POST /v1/vault/recover``.

Every secret is read hidden, goes once, and is never said back. The chat's code runs on a stand-in for the Core's answers
and, at the end, on the real route and a real vault. The real ``getpass`` needs a console, so hidden reads are stand-ins
here; a pipe's lines are the real path."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from puffpai_core import cli, termchat

CODE = "CDFHJ-KLMNP-QRTVW-XY234-56789"
NEW_CODE = "23456-78923-45678-92345-67892"
PIN = "hazel-otter-river"
OTHER = "marble-finch-gate"
SAID_BACK = "9911443322"                 # what she might type after the command, in view


class Answers:
    """The Core's answers by path. ``calls`` keeps a copy of each body; ``bodies`` the object the chat sent."""

    def __init__(self, table=None, status=None):
        self.table = {"/v1/vault/status": {"state": "locked"}, "/v1/consent?status=pending": [], **(table or {})}
        self.status, self.calls, self.bodies, self.streams = status or {}, [], [], []

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        self.calls.append((method, path, dict(body) if isinstance(body, dict) else body))
        self.bodies.append(body)
        got = self.table.get(path, {"error": "no such thing"})
        if isinstance(got, BaseException):
            raise got
        return self.status.get(path, 200), got

    def stream(self, path, body):
        self.streams.append((path, dict(body)))
        return 200, None, iter([{"type": "done", "text": ""}])

    def posted(self, path="/v1/vault/recover"):
        return [c for c in self.calls if c[0] == "POST" and c[1] == path]


def _chat(api, keys=(), hidden=(), term=False):
    out = io.StringIO()
    keys, hidden = iter(keys), iter(hidden)
    prompts = []

    def read(it):
        def go(prompt=""):
            prompts.append(prompt)
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None
        return go
    look = termchat.Look(out, env={}, tty=lambda s: term, vt_on=lambda s: term)
    chat = termchat.Chat(api, read_line=read(keys), read_hidden=read(hidden), open_url=lambda u: None, sleep=lambda s: None,
                         look=look)
    return chat, out, prompts


def _ok():
    return {"/v1/vault/recover": {"state": "open", "recovery_code": NEW_CODE, "note": "Write this new code down."},
            "/v1/vault/recovery-code/test": {"ok": True}}


# -- the three hidden reads -----------------------------------------------------------------------------------------------
def test_recover_asks_hidden_and_sends_once_then_drops_them():
    api = Answers(_ok())
    chat, out, prompts = _chat(api, hidden=[CODE, PIN, PIN, ""])
    assert chat.cmd_vault("recover") == 0
    assert prompts[:3] == ["  " + termchat.RECOVER_CODE_PROMPT, "  " + termchat.RECOVER_PIN_PROMPT, "  " + termchat.RECOVER_AGAIN_PROMPT]
    assert api.posted() == [("POST", "/v1/vault/recover", {"code": CODE, "new_pin": PIN})]
    sent = [b for c, b in zip(api.calls, api.bodies) if c[1] == "/v1/vault/recover"][0]
    assert sent == {}                                              # cleared as soon as it went
    said = out.getvalue()
    assert PIN not in said and CODE not in said and said.count(NEW_CODE) == 1 and "Write this new code down." in said
    assert termchat.RECOVER_DONE[0] in said and termchat.RECOVER_NOT_CHECKED in said


def test_recover_never_uses_the_chats_input_or_a_chat_turn():
    api = Answers(_ok())
    chat, _, _ = _chat(api, keys=[], hidden=[CODE, PIN, PIN, ""])
    chat.read_line = lambda _p="": pytest.fail("a secret was read by the chat's input")
    assert chat.command("/vault recover") == 0
    assert api.streams == []


def test_the_pins_differ_and_nothing_is_sent():
    api = Answers(_ok())
    chat, out, _ = _chat(api, hidden=[CODE, PIN, OTHER])
    assert chat.cmd_vault("recover") == 1
    assert api.posted() == [] and termchat.RECOVER_MISMATCH in out.getvalue()
    assert PIN not in out.getvalue() and OTHER not in out.getvalue() and CODE not in out.getvalue()


@pytest.mark.parametrize("weak", ["", "qx7", "12345678", "aaaaaaaaaa", "qwertyuiop"])
def test_a_weak_pin_is_refused_before_anything_goes(weak):
    api = Answers(_ok())
    chat, out, prompts = _chat(api, hidden=[CODE, weak])
    assert chat.cmd_vault("recover") == 1
    assert api.posted() == [] and len(prompts) == 2 and termchat.RECOVER_STILL_WORKS in out.getvalue()
    assert not weak or weak not in out.getvalue()


def test_no_code_typed_changes_nothing():
    api = Answers(_ok())
    chat, out, prompts = _chat(api, hidden=[""])
    assert chat.cmd_vault("recover") == 1
    assert api.posted() == [] and len(prompts) == 1 and termchat.RECOVER_NO_CODE in out.getvalue()


def test_no_vault_yet_asks_nothing():
    chat, out, prompts = _chat(Answers({"/v1/vault/status": {"state": "not_set_up"}}))
    assert chat.cmd_vault("recover") == 1
    assert prompts == [] and termchat.RECOVER_NO_VAULT in out.getvalue()


# -- what she typed in view ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("line", [f"/vault recover {SAID_BACK}", f"/VAULT RECOVER {SAID_BACK}", f"/vault recover {SAID_BACK} {SAID_BACK}"])
def test_words_after_it_are_ignored_and_never_said_back(line):
    api = Answers(_ok())
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN, ""])
    chat.command(line)
    assert SAID_BACK not in out.getvalue() and termchat.RECOVER_ARG in out.getvalue()
    assert api.posted() == [("POST", "/v1/vault/recover", {"code": CODE, "new_pin": PIN})]        # only what was read hidden


@pytest.mark.parametrize("line", [f"/vault recover{SAID_BACK}", f"/recover{SAID_BACK}", f"/vaultrecover{SAID_BACK}",
                                  f"/recover {SAID_BACK}", f"/unlock{SAID_BACK}", f"/vault {SAID_BACK}"])
def test_stuck_together_it_is_no_command_and_nothing_is_said_back(line):
    api = Answers(_ok())
    chat, out, prompts = _chat(api)
    chat.command(line)
    assert SAID_BACK not in out.getvalue() and api.posted() == [] and prompts == []


@pytest.mark.parametrize("argv", [["vault", "recover", SAID_BACK], ["vault", "recover", f"--pin={SAID_BACK}"],
                                  ["vault", "recover", "--code", SAID_BACK], ["vault", f"--pin={SAID_BACK}", "recover"]])
def test_a_shell_word_or_flag_is_taken_and_never_said_back(argv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(termchat, "run_verb", lambda fn, arg: seen.append((fn, arg)) or 0)
    assert cli.main(argv) == 0
    captured = capsys.readouterr()
    assert SAID_BACK not in captured.out + captured.err
    fn, arg = seen[0]
    assert fn == "cmd_vault"
    api = Answers(_ok())
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN, ""])
    assert chat.cmd_vault(arg) == 0 and SAID_BACK not in out.getvalue()
    assert api.posted() == [("POST", "/v1/vault/recover", {"code": CODE, "new_pin": PIN})]


def test_another_commands_unknown_flag_is_still_refused(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["list", "--nope"])
    assert stop.value.code == 2 and capsys.readouterr().err == cli.NOT_TAKEN + "\n"


def test_a_flag_after_vault_alone_says_how_and_never_says_it_back(monkeypatch, capsys):
    api = Answers()
    chat, out, _ = _chat(api)
    monkeypatch.setattr(termchat, "run_verb", lambda fn, arg: chat.cmd_vault(arg))
    assert cli.main(["vault", f"--code={SAID_BACK}"]) == 1
    captured = capsys.readouterr()
    assert SAID_BACK not in captured.out + captured.err + out.getvalue()


# -- the Core's own words ---------------------------------------------------------------------------------------------
def test_a_wrong_code_says_the_cores_words():
    api = Answers({"/v1/vault/recover": {"error": "That didn't match.", "code": "wrong"}}, status={"/v1/vault/recover": 401})
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN])
    assert chat.cmd_vault("recover") == 1
    assert "That didn't match." in out.getvalue() and "Nothing was changed." in out.getvalue()
    assert PIN not in out.getvalue() and CODE not in out.getvalue()


def test_too_many_tries_says_the_cores_words():
    line = "Too many tries. Try again in 5 seconds."
    api = Answers({"/v1/vault/recover": {"error": line, "code": "throttled", "retry_after": 5}}, status={"/v1/vault/recover": 429})
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN])
    assert chat.cmd_vault("recover") == 1 and line in out.getvalue()


@pytest.mark.parametrize("body, plain", [({"error": "x"}, True), ({"error": "x", "code": "sign_in"}, True), ("<html>", True),
                                         ({"error": "That didn't match.", "code": "wrong"}, False)])
def test_only_the_cores_sign_in_refusal_is_the_terminal_key_line(body, plain):
    assert cli.owner_words(401, body) == ({"error": cli.KEY_REFUSED} if plain else body)
    assert cli.owner_words(403, body) == body


def test_a_wrong_pin_at_unlock_says_the_cores_words_too():
    api = Answers({"/v1/vault/unlock": cli.owner_words(401, {"error": "That didn't match.", "code": "wrong"})},
                  status={"/v1/vault/unlock": 401})
    chat, out, _ = _chat(api, hidden=[PIN])
    assert chat.cmd_unlock("") is False and "That didn't match." in out.getvalue() and cli.KEY_REFUSED not in out.getvalue()


@pytest.mark.parametrize("trouble, status", [(ConnectionResetError(), 200), (TimeoutError(), 200), ({"error": "It broke."}, 500)])
def test_a_cut_reply_says_the_new_pin_may_already_work(trouble, status):
    api = Answers({"/v1/vault/recover": trouble}, status={"/v1/vault/recover": status})
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN])
    assert chat.cmd_vault("recover") == 1 and termchat.RECOVER_MAYBE in out.getvalue()
    assert [b for c, b in zip(api.calls, api.bodies) if c[1] == "/v1/vault/recover"][0] == {}


# -- the new code ----------------------------------------------------------------------------------------------------
def test_typed_back_right_it_goes_off_the_screen():
    api = Answers(_ok())
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN, NEW_CODE], term=True)
    assert chat.cmd_vault("recover") == 0
    assert api.posted("/v1/vault/recovery-code/test") == [("POST", "/v1/vault/recovery-code/test", {"code": NEW_CODE})]
    from puffpai_core.wizard.terminal import CLEAR
    said = out.getvalue()
    assert CLEAR in said and NEW_CODE not in said[said.index(CLEAR):]


def test_typed_back_wrong_three_times_the_code_stands_and_the_pin_is_not_kept():
    api = Answers({**_ok(), "/v1/vault/recovery-code/test": {"ok": False}})
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN, "x", "y", "z"])
    assert chat.cmd_vault("recover") == 0
    assert len(api.posted("/v1/vault/recovery-code/test")) == 3 and termchat.RECOVER_BACK_LAST in out.getvalue()
    assert len([c for c in api.calls if c[1] == "/v1/vault/recovery-code/rotate"]) == 0


def test_no_new_code_in_the_reply_is_said():
    api = Answers({"/v1/vault/recover": {"state": "open"}})
    chat, out, _ = _chat(api, hidden=[CODE, PIN, PIN])
    assert chat.cmd_vault("recover") == 1 and termchat.RECOVER_NO_NEW_CODE in out.getvalue()


# -- with no console ---------------------------------------------------------------------------------------------------
def test_from_a_pipe_each_is_one_line_never_shown_and_the_new_code_is_not_asked_back(monkeypatch):
    monkeypatch.setattr(termchat.sys, "stdin", io.StringIO(f"{CODE}\r\n{PIN}\n{PIN}\n"))
    api = Answers(_ok())
    out = io.StringIO()
    chat = termchat.Chat(api, look=termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False))
    assert chat.read_hidden is termchat.getpass.getpass
    assert chat.cmd_vault("recover") == 0
    assert api.posted() == [("POST", "/v1/vault/recover", {"code": CODE, "new_pin": PIN})]
    assert PIN not in out.getvalue() and CODE not in out.getvalue()
    assert termchat.RECOVER_NOT_CHECKED in out.getvalue() and api.posted("/v1/vault/recovery-code/test") == []


def test_an_empty_pipe_changes_nothing(monkeypatch):
    monkeypatch.setattr(termchat.sys, "stdin", io.StringIO(""))
    api = Answers(_ok())
    chat = termchat.Chat(api, look=termchat.Look(io.StringIO(), env={}, tty=lambda s: False, vt_on=lambda s: False))
    assert chat.cmd_vault("recover") == 1 and api.posted() == []


# -- the command list ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("word", ["recover", "forgot", "lost"])
def test_help_finds_it(word):
    want, hits = termchat.find_commands(word)
    assert want == [word] and "vault" in [h["name"] for h in hits]


def test_the_search_words_stay_free_of_digits():
    assert not any(c.isdigit() for words in termchat.COMMAND_WORDS.values() for c in words)


def test_status_says_how_to_get_back_in():
    chat, out, _ = _chat(Answers())
    assert chat.cmd_vault("") == 0 and "/vault recover" in out.getvalue()


# -- the real route, a real vault -------------------------------------------------------------------------------------
class Route:
    """``Api.call`` on the Flask test client, the terminal's way of reading a 401 included."""

    def __init__(self, client, token):
        self.client, self.headers = client, {"Authorization": f"Bearer {token}"}

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        got = self.client.open(path, method=method, json=body, headers=self.headers)
        return got.status_code, cli.owner_words(got.status_code, got.get_json(silent=True))


@pytest.fixture
def real(state):
    from puffpai_core.api import create_app
    from puffpai_core.zone1 import archivist
    app = create_app(state, port=51235)
    core = app.extensions["puffpai"]
    device = core.tokens.issue_device("laptop")
    client = app.test_client()
    made = client.post("/v1/vault/setup", json={"pin": PIN}, headers={"Authorization": f"Bearer {device}"})
    assert made.status_code == 201
    return types.SimpleNamespace(app=app, core=core, client=client, code=made.json["recovery_code"], device=device,
                                 route=Route(client, device), vault=archivist.for_state(state, core.bus).vault, state=state)


def _new_code_from(said):
    import re
    found = re.findall(r"[2-9CDFHJKLMNPQRTVWXY]{5}(?:-[2-9CDFHJKLMNPQRTVWXY]{5}){4}", said)
    assert len(found) == 1
    return found[0]


def test_recover_on_the_real_route_the_old_pin_is_out_and_the_new_one_works(real):
    chat, out, _ = _chat(real.route, hidden=[real.code, OTHER, OTHER, ""])
    assert chat.cmd_vault("recover") == 0
    said = out.getvalue()
    new = _new_code_from(said)
    assert new != real.code and OTHER not in said and PIN not in said and real.code not in said
    real.vault.lock("owner")
    key = {"Authorization": f"Bearer {real.device}"}
    assert real.client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=key).status_code == 401
    assert real.client.post("/v1/vault/unlock", json={"method": "recovery", "secret": real.code}, headers=key).status_code == 401
    assert real.client.post("/v1/vault/unlock", json={"method": "pin", "secret": OTHER}, headers=key).status_code == 200
    assert real.client.post("/v1/vault/recovery-code/test", json={"code": new}, headers=key).json["ok"] is True


def test_what_waited_in_the_inbox_goes_into_the_vault_and_is_said(real):
    key = {"Authorization": f"Bearer {real.device}"}
    real.vault.lock("owner")
    sealed = real.client.put("/v1/vault/secrets/MY_KEY", json={"value": "made-up-value", "seal_if_locked": True}, headers=key)
    assert sealed.status_code == 202
    assert real.client.get("/v1/vault/status", headers=key).json["inbox_waiting"] == 1
    chat, out, _ = _chat(real.route, hidden=[real.code, OTHER, OTHER, ""])
    assert chat.cmd_vault("recover") == 0
    assert "1 item kept while your vault was locked went into it." in out.getvalue()
    status = real.client.get("/v1/vault/status", headers=key).json
    assert status["inbox_waiting"] == 0 and status["state"] == "open" and status["items"] == 1


def test_a_wrong_code_on_the_real_route_says_the_cores_words_and_keeps_everything(real):
    wrong = "22222-22222-22222-22222-22222"
    chat, out, _ = _chat(real.route, hidden=[wrong, OTHER, OTHER])
    assert chat.cmd_vault("recover") == 1
    said = out.getvalue()
    assert "That didn't match." in said and cli.KEY_REFUSED not in said and wrong not in said and OTHER not in said
    assert real.vault.test_recovery(real.code) is True


def test_a_weak_pin_on_the_real_route_is_refused_by_the_core_too(real):
    got = real.client.post("/v1/vault/recover", json={"code": real.code, "new_pin": "12345678"},
                           headers={"Authorization": f"Bearer {real.device}"})
    assert got.status_code == 400 and got.json["code"] == "weak" and "12345678" not in got.get_data(as_text=True)
    assert real.vault.test_recovery(real.code) is True


def test_nothing_typed_is_left_in_the_bot_folder(real):
    chat, _, _ = _chat(real.route, hidden=[real.code, OTHER, OTHER, ""])
    assert chat.cmd_vault("recover") == 0
    for path in real.state.rglob("*"):
        if path.is_file():
            blob = path.read_bytes()
            assert OTHER.encode() not in blob and real.code.encode() not in blob, path


def test_only_a_paired_device_can_call_the_route(real):
    body = {"code": real.code, "new_pin": OTHER}
    assert real.client.post("/v1/vault/recover", json=body).status_code == 401
    for token in (real.core.tokens.issue_owner_key("a program"), real.core.tokens.issue_package_token("x")):
        assert real.client.post("/v1/vault/recover", json=body, headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert real.vault.test_recovery(real.code) is True


# -- the command line never says back what she typed ------------------------------------------------------------------------
TYPED = [["vault", f"--help={SAID_BACK}"], ["vault", f"--he={SAID_BACK}"], ["vault", "recover", f"--h={SAID_BACK}"],
         [f"--help={SAID_BACK}", "vault"], ["--pin", SAID_BACK, "vault", "recover"], [f"--pin={SAID_BACK}"],
         [f"vault{SAID_BACK}"], [f"recover{SAID_BACK}"], [f"unlock{SAID_BACK}"], [SAID_BACK],
         ["keys", "add", "anthropic", f"--value={SAID_BACK}"], ["keys", f"--value={SAID_BACK}"],
         ["keys", "add", "anthropic", f"-v{SAID_BACK}"], ["bridge", "open", f"--pin={SAID_BACK}"], ["chat", f"--pin={SAID_BACK}"],
         ["uninstall", f"--pin={SAID_BACK}"], ["start", f"--port={SAID_BACK}"], ["store", SAID_BACK], ["bridge", SAID_BACK]]


def _would_run(argv):
    """Whether these words get past the parser, read without running anything. Asked before each ``main`` below: a case
    that gets past it would run that command for real, and ``start`` opens a console window."""
    try:
        args, unknown = cli.build_parser().parse_known_args(argv)
    except SystemExit:
        return False
    return not unknown or args.command == "vault"


@pytest.mark.parametrize("argv", TYPED, ids=lambda a: "-".join(a).replace(SAID_BACK, "M"))
def test_the_parser_says_one_fixed_line(argv, capsys):
    assert not _would_run(argv)
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        cli.main(argv)
    captured = capsys.readouterr()
    assert stop.value.code == 2 and captured.out == "" and captured.err == cli.NOT_TAKEN + "\n"


def test_as_a_real_process_nothing_typed_comes_back(tmp_path):
    """The real program, in its own process and with no window, with a bot folder and home of the test's own."""
    env = {**os.environ, "USERPROFILE": str(tmp_path), "HOME": str(tmp_path), "LOCALAPPDATA": str(tmp_path / "local"),
           "PUFFPAI_STATE": str(tmp_path / "state"), "PYTHONPATH": str(Path(cli.__file__).parents[1]),
           "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    for argv in TYPED:
        assert not _would_run(argv)
        got = subprocess.run([sys.executable, "-m", "puffpai_core.cli", *argv], env=env, cwd=tmp_path, capture_output=True, text=True,
                             stdin=subprocess.DEVNULL, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert got.returncode == 2 and got.stdout == "" and got.stderr.strip() == cli.NOT_TAKEN, argv[0] + " " + str(len(argv))


@pytest.mark.parametrize("words", [["serve", "--port"], ["start", "--port"], ["app", "--port"], ["setup", "--port"], ["pair", "--port"],
                                   ["serve", "--pages-port"], ["serve", "--proxy-port"], ["setup", "--pages-port"], ["setup", "--proxy-port"]])
@pytest.mark.parametrize("value", ["65536", "-1", SAID_BACK, "x"])
def test_a_port_that_cant_be_one_is_refused(words, value):
    assert not _would_run([*words, value] if value != "-1" else [*words[:-1], f"{words[-1]}=-1"])


@pytest.mark.parametrize("value, port", [("0", 0), ("1", 1), ("65535", 65535)])
def test_a_port_that_can_be_one_is_taken(value, port):
    assert cli.build_parser().parse_args(["serve", "--proxy-port", value]).proxy_port == port


def test_every_commands_parser_is_the_fixed_one():
    parser = cli.build_parser()
    assert isinstance(parser, cli.Parser)
    subs = parser._subparsers._group_actions[0].choices
    assert "vault" in subs and all(isinstance(s, cli.Parser) for s in subs.values())


def test_help_itself_still_works(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["vault", "--help"])
    assert stop.value.code == 0 and "usage: puffpai vault" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["vault", "lock", "--anything"], ["vault", "--anything", "lock"], ["vault", f"--pin={SAID_BACK}", "lock"],
                                  ["vault", "lock", SAID_BACK], ["vault", "status", "--anything"], ["vault", "status", SAID_BACK]])
def test_a_vault_verb_with_something_it_doesnt_know_does_not_run(argv, monkeypatch, capsys):
    api = Answers({"/v1/vault/lock": {}})
    chat, out, _ = _chat(api)
    monkeypatch.setattr(termchat, "run_verb", lambda fn, arg: getattr(chat, fn)(arg))
    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert SAID_BACK not in captured.out + captured.err + out.getvalue() and termchat.VAULT_ARG in out.getvalue()
    assert api.calls == []


def test_vault_unlock_with_a_flag_is_refused_and_sends_nothing(monkeypatch, capsys):
    api = Answers()
    chat, out, prompts = _chat(api)
    monkeypatch.setattr(termchat, "run_verb", lambda fn, arg: getattr(chat, fn)(arg))
    assert cli.main(["vault", "unlock", f"--pin={SAID_BACK}"]) == 1
    captured = capsys.readouterr()
    assert SAID_BACK not in captured.out + captured.err + out.getvalue() and termchat.PIN_ARG in out.getvalue()
    assert api.calls == [] and prompts == []


@pytest.mark.parametrize("line, sent", [("/vault lock", True), ("/vault", False), ("/vault status", False), (f"/vault lock {SAID_BACK}", False)])
def test_the_vault_verbs_alone_still_run(line, sent):
    api = Answers({"/v1/vault/lock": {}})
    chat, out, _ = _chat(api)
    chat.command(line)
    assert bool(api.posted("/v1/vault/lock")) is sent and SAID_BACK not in out.getvalue()


# -- plain words in the chat -----------------------------------------------------------------------------------------------
STOPPED = [f"unlock {SAID_BACK}", f"vault recover {SAID_BACK}", f"UNLOCK {SAID_BACK}", f"lock {SAID_BACK}", f"vault {SAID_BACK}", f"recover {SAID_BACK}",
           f"pin {SAID_BACK}", f"pin: {SAID_BACK}", f"pin={SAID_BACK}", f"unlock{SAID_BACK}", f"'unlock {SAID_BACK}'",
           "unlock hazel otter river", "unlock hazel-otter-river", "pin=hazel-otter-river", "vault recover my code please"]
ASKED = ["how do I unlock my vault", "unlock my vault", "lock the vault", "vault status", "what is a vault", "pin this",
         "recover it", "locked out of my vault today", "pinned notes for tomorrow please", "vaults are safe places, right",
         "recovery takes how long, about", "what does 1234 mean", "unlockable"]


@pytest.mark.parametrize("line", STOPPED)
def test_a_vault_word_with_a_secret_in_it_stays_here(line):
    api = Answers()
    chat, out, _ = _chat(api, keys=[line])
    assert chat.run() == 0
    said = out.getvalue()
    assert api.streams == [] and SAID_BACK not in said and "hazel" not in said and termchat.SECRET_NOT_SENT in said
    assert not [c for c in api.calls if isinstance(c[2], dict) and "text" in c[2]]


def test_it_says_how_to_do_it():
    assert "/unlock" in termchat.SECRET_NOT_SENT and "/vault recover" in termchat.SECRET_NOT_SENT and "hidden" in termchat.SECRET_NOT_SENT


@pytest.mark.parametrize("line", ASKED)
def test_an_ordinary_sentence_reaches_the_brain(line):
    api = Answers()
    chat, out, _ = _chat(api, keys=[line])
    assert chat.run() == 0
    assert api.streams == [("/v1/chat/stream", {"text": line})] and termchat.SECRET_NOT_SENT not in out.getvalue()


@pytest.mark.parametrize("text", [f"unlock {SAID_BACK}", f"/unlock {SAID_BACK}", f"/unlock{SAID_BACK}"])
def test_chat_once_stops_it_too(text):
    api = Answers()
    chat, out, _ = _chat(api)
    assert chat.once(text) is False
    assert api.streams == [] and api.calls == [] and SAID_BACK not in out.getvalue()


# -- keys add -------------------------------------------------------------------------------------------------------------
PROVIDER_PATH = "/v1/toolsmith/keys/anthropic"


@pytest.mark.parametrize("status, body, line", [(202, {"saved": False, "line": "The route's own line."}, "The route's own line."),
                                                (200, {"saved": True, "line": "The route's own line."}, "The route's own line."),
                                                (202, {}, termchat.KEY_KEPT[202]), (200, {}, termchat.KEY_KEPT[200])])
def test_keys_add_says_the_routes_words(status, body, line):
    chat, out, _ = _chat(Answers({PROVIDER_PATH: body}, status={PROVIDER_PATH: status}), hidden=["sk-made-up"])
    assert chat.cmd_keys("add anthropic") == 0
    assert line in out.getvalue() and "sk-made-up" not in out.getvalue()


def test_keys_add_on_the_real_route_says_sealed_while_locked_and_saved_when_open(real):
    real.vault.lock("owner")
    chat, out, _ = _chat(real.route, hidden=["sk-made-up"])
    assert chat.cmd_keys("add anthropic") == 0
    assert "Sealed in your vault's inbox." in out.getvalue() and "aved in your vault" not in out.getvalue()
    assert real.client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN},
                            headers={"Authorization": f"Bearer {real.device}"}).status_code == 200
    chat, out, _ = _chat(real.route, hidden=["sk-made-up"])
    assert chat.cmd_keys("add anthropic") == 0 and "Saved in your vault." in out.getvalue() and "Sealed" not in out.getvalue()


def test_keys_add_when_refused_says_so_and_not_saved():
    chat, out, _ = _chat(Answers({PROVIDER_PATH: {"error": "There is no such provider for Toolsmith."}}, status={PROVIDER_PATH: 404}), hidden=["sk-made-up"])
    assert chat.cmd_keys("add anthropic") == 1 and "The key was not saved: There is no such provider" in out.getvalue()


def test_the_end_of_input_at_keys_add_saves_nothing():
    api = Answers()
    chat, out, _ = _chat(api, hidden=[])                           # the stand-in raises EOFError, as getpass does
    assert chat.cmd_keys("add anthropic") == 1
    assert "Nothing was saved." in out.getvalue() and api.calls == []


def _no_console(api):
    out = io.StringIO()
    chat = termchat.Chat(api, look=termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False))
    assert chat.read_hidden is termchat.getpass.getpass
    return chat, out


def test_keys_add_from_a_pipe_takes_one_line_and_never_waits_on_the_console(monkeypatch):
    monkeypatch.setattr(termchat.sys, "stdin", io.StringIO("sk-made-up\r\n"))
    api = Answers({PROVIDER_PATH: {"line": "Saved in your vault."}})
    chat, out = _no_console(api)
    assert chat.cmd_keys("add anthropic") == 0
    assert api.calls == [("PUT", PROVIDER_PATH, {"value": "sk-made-up"})] and "sk-made-up" not in out.getvalue()


def test_vault_unlock_from_a_pipe_takes_one_line_and_never_waits_on_the_console(monkeypatch):
    monkeypatch.setattr(termchat.sys, "stdin", io.StringIO(f"{PIN}\r\n"))
    api = Answers({"/v1/vault/unlock": {}})
    chat, out = _no_console(api)
    assert chat.cmd_vault("unlock") == 0
    assert api.posted("/v1/vault/unlock") == [("POST", "/v1/vault/unlock", {"method": "pin", "secret": PIN})] and PIN not in out.getvalue()


def _closed():
    closed = io.StringIO("")
    closed.close()
    return closed


@pytest.mark.parametrize("stdin", [lambda: None, _closed, lambda: io.StringIO(""), lambda: io.TextIOWrapper(io.BytesIO(b"\xff\xfe\n"), encoding="utf-8")],
                         ids=["none", "closed", "empty", "bad-bytes"])
@pytest.mark.parametrize("fn, rest, line", [("cmd_vault", "recover", termchat.RECOVER_NO_CODE), ("cmd_vault", "unlock", "No PIN typed."),
                                            ("cmd_keys", "add anthropic", "Nothing was saved.")])
def test_with_no_usable_input_it_says_a_plain_line_and_sends_nothing(stdin, fn, rest, line, monkeypatch):
    monkeypatch.setattr(termchat.sys, "stdin", stdin())
    api = Answers()
    chat, out = _no_console(api)
    assert getattr(chat, fn)(rest) == 1
    assert line in out.getvalue()
    assert not [c for c in api.calls if c[0] != "GET"]


@pytest.mark.parametrize("trouble", [RuntimeError("input(): lost sys.stdin"), ValueError("I/O operation on closed file.")])
def test_the_chat_with_no_stdin_says_bye(trouble):
    def gone(_prompt=""):
        raise trouble
    api = Answers()
    chat, out, _ = _chat(api)
    chat.read_line = gone
    assert chat.run() == 0 and termchat.BYE in out.getvalue() and api.streams == []


# -- the PIN's wait and a recovery ----------------------------------------------------------------------------------------
def _wrong_pins(real, times=8):
    key = {"Authorization": f"Bearer {real.device}"}
    return [real.client.post("/v1/vault/unlock", json={"method": "pin", "secret": "Wrong#pin99"}, headers=key).status_code for _ in range(times)]


def test_after_a_recovery_the_new_pin_works_at_once(real):
    real.vault.lock("owner")
    assert _wrong_pins(real)[-1] == 429 and real.vault.retry_after() > 0
    chat, out, _ = _chat(real.route, hidden=[real.code, OTHER, OTHER, ""])
    assert chat.cmd_vault("recover") == 0 and termchat.RECOVER_DONE[0] in out.getvalue()
    real.vault.lock("owner")
    got = real.client.post("/v1/vault/unlock", json={"method": "pin", "secret": OTHER}, headers={"Authorization": f"Bearer {real.device}"})
    assert got.status_code == 200


def test_a_wrong_code_clears_no_wait_and_keeps_its_own(real):
    real.vault.lock("owner")
    assert _wrong_pins(real)[-1] == 429
    key = {"Authorization": f"Bearer {real.device}"}
    wrong = {"code": "22222-22222-22222-22222-22222", "new_pin": OTHER}
    codes = [real.client.post("/v1/vault/recover", json=wrong, headers=key).status_code for _ in range(8)]
    assert codes[0] == 401 and codes[-1] == 429
    assert real.vault.retry_after() > 0 and real.vault._attempts["recovery"][0] >= 6
    right = real.client.post("/v1/vault/recover", json={"code": real.code, "new_pin": OTHER}, headers=key)
    assert right.status_code == 429 and real.vault.is_open() is False            # even the right code waits its turn
    assert real.vault.retry_after() > 0


def test_a_recovery_that_did_not_finish_clears_no_wait(real, monkeypatch):
    from puffpai_core.zone1.vault import Refused

    def broke(_new_pin):
        raise Refused("It broke.")
    real.vault.lock("owner")
    assert _wrong_pins(real)[-1] == 429
    monkeypatch.setattr(real.vault, "_rekey", broke)
    got = real.client.post("/v1/vault/recover", json={"code": real.code, "new_pin": OTHER}, headers={"Authorization": f"Bearer {real.device}"})
    assert got.status_code != 200 and "pin" in real.vault._attempts


def test_opening_with_the_recovery_code_alone_clears_no_pin_wait(real):
    real.vault.lock("owner")
    assert _wrong_pins(real)[-1] == 429
    key = {"Authorization": f"Bearer {real.device}"}
    assert real.client.post("/v1/vault/unlock", json={"method": "recovery", "secret": real.code}, headers=key).status_code == 200
    assert "pin" in real.vault._attempts
