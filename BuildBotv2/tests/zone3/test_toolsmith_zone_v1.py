# Harvested from PuffPAIBuildBot@63c8a037:tests/test_toolsmith_zone.py (adapted: v2 places; the toolsmith_run tests move to lane L6)
"""Toolsmith's zone (2026-10-14): "Toolsmith shouldn't have access to the internal parts, as it's
the agent that touches the outside." Each denied place is refused with one plain line, even with her
Yes; the allowed places work; her account name never reaches the model. Nothing here goes online or
starts a shell."""

from __future__ import annotations

import json
import os

import pytest

from puffpai_core.zone3 import toolsmith_zone as Z


@pytest.fixture
def places(monkeypatch, tmp_path, state):
    home = tmp_path / "home"
    appdata, local = home / "AppData" / "Roaming", home / "AppData" / "Local"
    for d in (home / ".ssh", home / ".aws", home / "Documents" / "myapp", home / "Desktop",
              appdata / "Microsoft" / "Credentials", appdata / "npm-cache" / "_cacache",
              local / "Google" / "Chrome" / "User Data",
              appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"):
        d.mkdir(parents=True)
    (home / ".ssh" / "id_rsa").write_text("not a key", encoding="utf-8")
    for k, v in (("USERPROFILE", home), ("HOME", home), ("APPDATA", appdata), ("LOCALAPPDATA", local)):
        monkeypatch.setenv(k, str(v))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    tools = tmp_path / "tools" / "nodejs"
    tools.mkdir(parents=True)
    (tools / "node.exe").write_text("", encoding="utf-8")
    real_which = Z.shutil.which
    monkeypatch.setattr(Z.shutil, "which", lambda n: str(tools / "node.exe") if n == "node"
                        else None if n in Z._TOOL_PROGRAMS else real_which(n))  # noqa: SLF001
    folder = state / "packages" / "worldmonitor"
    folder.mkdir(parents=True)
    (state / "packages" / "freshtable").mkdir()
    (state / "settings").mkdir()
    data = tmp_path / "data"
    (data / "My files").mkdir(parents=True)
    (state / "settings" / "folders.json").write_text(json.dumps({"data": str(data)}), encoding="utf-8")
    (state / "settings" / "toolsmith_model.json").write_text("{}", encoding="utf-8")
    vault = state / "vault"
    vault.mkdir()
    zone = Z.zone_for(str(folder))
    return {"home": str(home), "appdata": str(appdata), "local": str(local), "state": str(state),
            "folder": str(folder), "zone": zone, "tools": str(tools), "vault": str(vault), "data": str(data)}


def _refused(cmd, p, category):
    line, _out = Z.command_check(cmd, p["zone"])
    assert line == Z.refused_line(category), (cmd, line)


def test_sign_in_keys_are_refused(places):
    _refused("cat ~/.ssh/id_rsa", places, Z.KEYS)
    _refused('cat "$HOME/.aws/credentials"', places, Z.KEYS)
    _refused('cat "$APPDATA/Microsoft/Credentials/x"', places, Z.KEYS)
    _refused("Get-Content $env:APPDATA/Microsoft/Credentials/x", places, Z.KEYS)
    if os.name == "nt":
        _refused(r"type %APPDATA%\Microsoft\Credentials\x", places, Z.KEYS)
        _refused(r"Get-Content $env:APPDATA\Microsoft\Credentials\x", places, Z.KEYS)


def test_the_bots_own_places_are_refused(places):
    _refused(f'cd "{places["state"]}"', places, Z.STATE)
    _refused("cat ../../settings/toolsmith_model.json", places, Z.STATE)      # a .. escape
    _refused("cat ../freshtable/.env", places, Z.OTHER_APP)
    _refused("cd ..", places, Z.OTHER_APP)
    _refused(f'echo hi > "{os.path.join(places["data"], "notes.txt")}"', places, Z.DATA)
    _refused(f'ls "{places["vault"]}"', places, Z.VAULT)
    code = os.path.dirname(os.path.dirname(os.path.abspath(Z.__file__)))
    _refused(f'cat "{os.path.join(code, "zone3", "policy.py")}"', places, Z.CODE)
    _refused("python -m puffpai_core.cli list", places, Z.CODE)
    _refused("puffpai list", places, Z.CODE)


def test_her_own_folders_are_refused(places):
    _refused("cp .env ~/Documents/", places, Z.PERSONAL)
    _refused(f'ls "{places["local"]}/Google/Chrome/User Data"', places, Z.BROWSER)
    _refused(f'cp x.bat "{places["appdata"]}/Microsoft/Windows/Start Menu/Programs/Startup/"', places, Z.STARTUP)
    _refused("grep -r token ~", places, Z.WHOLE)
    _refused("tar czf all.tgz ~", places, Z.WHOLE)


def test_docker_mounts_of_her_home_or_keys_are_refused(places):
    _refused(f'docker run -v "{places["home"]}:/x" img', places, Z.WHOLE)
    _refused("docker run -v ~/.aws:/root/.aws img", places, Z.KEYS)
    _refused(f"docker run --mount type=bind,source={places['home']}/.ssh,target=/k img", places, Z.KEYS)
    assert Z.command_check("docker run -v appdata:/data -v ./conf:/conf img", places["zone"])[0] == ""


def test_a_compose_file_mounting_her_keys_is_refused(places):
    z, f = places["zone"], places["folder"]
    bad = "services:\n  app:\n    image: x\n    volumes:\n      - ~/.aws:/root/.aws\n"
    assert Z.compose_refusal(bad, f, z) == Z.refused_line(Z.KEYS)
    via_env = "services:\n  app:\n    image: x\n    volumes:\n      - ${KEYS}:/k\n"
    assert Z.compose_refusal(via_env, f, z, {"KEYS": "~/.ssh"}) == Z.refused_line(Z.KEYS)
    ok = "services:\n  app:\n    image: x\n    volumes:\n      - ./data:/data\n      - db:/v\n"
    assert Z.compose_refusal(ok, f, z) == ""


def test_a_container_holding_the_docker_engine_is_refused(places):
    z, f = places["zone"], places["folder"]
    for cmd in ("docker run -v /var/run/docker.sock:/var/run/docker.sock img",
                r"docker run -v \\.\pipe\docker_engine:\\.\pipe\docker_engine img", "docker run --privileged img"):
        assert Z.command_check(cmd, z)[0] == Z.refused_line(Z.ENGINE), cmd
    assert Z.compose_refusal("services:\n  a:\n    image: x\n    privileged: true\n", f, z) == Z.refused_line(Z.ENGINE)


def test_the_allowlist_works(places):
    z = places["zone"]
    for cmd in ("npm install", "npm ci --no-audit", "cat ./README.md", "cp .env.example .env",
                f'ls "{places["appdata"]}/npm-cache"', f'"{os.path.join(places["tools"], "node.exe")}" --version'):
        assert Z.command_check(cmd, z)[0] == "", cmd


def test_her_chosen_folder_is_allowed_but_not_its_parent(places):
    mine = os.path.join(places["home"], "Documents", "myapp")
    assert Z.folder_problem(mine) == ""
    z = Z.zone_for(mine)
    assert z.classify(os.path.join(mine, "src", "a.js"))[0] == Z.ALLOW
    assert z.classify(os.path.join(places["home"], "Documents")) == (Z.DENY, Z.PERSONAL)
    assert Z.command_check("cat ../notes.txt", z, mine)[0] == Z.refused_line(Z.PERSONAL)
    assert Z.folder_problem(places["home"]) == Z.refused_line(Z.WHOLE)
    assert Z.folder_problem(places["appdata"])


def test_her_name_is_scrubbed_from_outgoing_text_and_put_back_on_the_way_in():
    text = r"cloned into C:\Users\samplehost\x; /c/Users/samplehost/y; whoami: samplehost"
    out = Z.scrub(text, "samplehost")
    assert "samplehost" not in out and r"C:\Users\you\x" in out and "/c/Users/you/y" in out
    assert Z.unscrub(r"cd C:\Users\you\x", "samplehost") == r"cd C:\Users\samplehost\x"
    assert Z.scrub("a short name stays put", "al") == "a short name stays put"


def test_toolsmiths_shell_has_no_core_names_or_settings(places, tmp_path):
    base = {"PATH": os.pathsep.join([places["state"], places["tools"]]), "PUFFPAI_TOKEN": "x", "puffpai_api": "y",
            "HOME": places["home"]}
    env = Z.shell_env(base, scratch=str(tmp_path / "scratch"))
    assert not [k for k in env if k.upper().startswith("PUFFPAI")]
    assert env["PATH"] == places["tools"] and env["TEMP"] == str(tmp_path / "scratch")
