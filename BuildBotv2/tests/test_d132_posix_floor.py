"""D132: the floor rules on Linux and Mac, with real files and real links. Skipped on Windows."""

from __future__ import annotations

import os
import sys
import venv

import pytest

from puffpai_core.paths import data_root
from puffpai_core.zone3 import hook_guard, toolsmith_zone as Z
from puffpai_core.zone3.toolsmith_host import allowlist as A

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux and Mac places; Windows has its own cases")
SITE = os.path.join(".venv", "lib", f"python{sys.version_info[0]}.{sys.version_info[1]}", "site-packages")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake home folder, a bot folder and one try, none of them under each other."""
    home, state = tmp_path / "home", tmp_path / "state"
    folder = state / "packages" / "mytry"
    folder.mkdir(parents=True)
    (state / "settings").mkdir()
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)
    return home, state, folder


def _path(box, target, write=False):
    return Z.check(str(target), folder=str(box[2]), kind="path", write=write, zone=Z.zone_for(str(box[2]), state=str(box[1])))


def _cmd(box, command):
    return Z.check(command, folder=str(box[2]), kind="command", cwd=str(box[2]),
                   zone=Z.zone_for(str(box[2]), state=str(box[1])))


@pytest.mark.parametrize("rel", [
    ".ssh/id_rsa", ".gnupg/k", ".aws/credentials", ".azure/t", ".kube/config", ".git-credentials", ".netrc",
    ".config/gcloud/c", ".docker/config.json", ".local/share/keyrings/k", ".mozilla/p", ".config/google-chrome/p",
    ".config/chromium/p", "Library/Keychains/k", "Library/Application Support/Google/Chrome/p",
    "Library/Application Support/Firefox/p", "snap/firefox/p", ".bashrc", ".profile"])
def test_her_sign_in_places_are_refused(box, rel):
    home = box[0]
    (home / rel).parent.mkdir(parents=True, exist_ok=True)
    (home / rel).write_text("made up", encoding="utf-8")
    assert _path(box, home / rel).verdict == "deny"
    assert _path(box, home / rel, write=True).verdict == "deny"
    assert _cmd(box, f'python app.py "{home / rel}"').verdict == "deny"
    assert _cmd(box, f'python app.py "/{home / rel}"').verdict == "deny", "two slashes open the same file"
    assert _cmd(box, f'python app.py "~/{rel}"').verdict == "deny"
    assert _cmd(box, f'python app.py "$HOME/{rel}"').verdict == "deny"


def test_her_bot_folders_and_the_machine_key_are_refused_as_her_keys(box):
    root = data_root()
    root.joinpath("other", "vault").mkdir(parents=True)
    root.joinpath("machine.key").write_text("made up", encoding="utf-8")
    keys = Z.refused_line(Z.KEYS)
    for place in (root / "machine.key", root / "other" / "vault" / "x", root):
        for write in (False, True):
            got = _path(box, place, write=write)
            assert got.verdict == "deny" and got.line == keys, place
    assert _cmd(box, f"python app.py {root / 'machine.key'}").verdict == "deny"
    assert _path(box, root.parent).verdict == "deny", "a folder that holds them"


def test_the_xdg_folders_she_moved_are_refused_too(box, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "shared"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    for place in ("cfg/gcloud/c", "cfg/google-chrome/p", "shared/keyrings/k", "shared/puffpai/machine.key", "run/agent"):
        assert _path(box, tmp_path / place).verdict == "deny", place


def test_a_write_through_a_link_out_of_the_try_never_passes(box, tmp_path):
    home, _state, folder = box
    (home / ".ssh").mkdir()
    away = tmp_path / "away"
    away.mkdir()
    (folder / "keys").symlink_to(home / ".ssh")
    (folder / "out").symlink_to(away)
    (folder / "sys").symlink_to("/etc")
    (folder / "file").symlink_to(away / "f.txt")
    assert _path(box, folder / "keys" / "x", write=True).verdict == "deny"
    assert _path(box, folder / "sys" / "x", write=True).verdict == "deny"
    assert _path(box, folder / "out" / "x", write=True).verdict == "ask"
    assert _path(box, folder / "file", write=True).verdict == "ask"
    assert _cmd(box, "cp a.txt keys/x").verdict == "deny"
    assert _cmd(box, "cp a.txt sys/x").verdict == "deny"
    assert _cmd(box, "cp a.txt out/x").verdict == "ask"
    (folder / "inside").mkdir()
    (folder / "stays").symlink_to(folder / "inside")
    assert _path(box, folder / "stays" / "x", write=True).verdict == "allow"


def test_start_hooks_under_lib_python_are_refused(box):
    site = box[2] / SITE
    site.mkdir(parents=True)
    for name in ("a.pth", "sitecustomize.py", "usercustomize.py"):
        assert _path(box, site / name).verdict == "deny", name
        assert _path(box, site / name, write=True).verdict == "deny", name
    assert _path(box, site / "plain.py", write=True).verdict == "deny", "no write into site-packages"
    assert _cmd(box, f"cp a.pth {SITE}/").verdict == "deny"


def test_the_guard_sees_the_cores_own_site_folder_and_undoes_a_hook_in_the_try(box):
    site = box[2] / SITE
    site.mkdir(parents=True)
    wanted = {os.path.realpath(p) for p in sys.path if p.endswith("site-packages")}
    assert wanted & {os.path.realpath(d) for d in hook_guard.core_site_dirs()}
    stopped = []
    with hook_guard.HookGuard(str(box[2]), core_sites=[], stop_try=stopped.append) as guard:
        (site / "evil.pth").write_text("import os\n", encoding="utf-8")
    assert guard.result["action"] == "stopped_try" and stopped and not (site / "evil.pth").exists()


@pytest.mark.parametrize("place", ["/etc/x", "/usr/x", "/usr/local/bin/x", "/boot/x", "/sys/x", "/proc/x", "/dev/x",
                                   "/Library/LaunchDaemons/x", "/System/x", "/lib/x"])
def test_system_folders_are_never_written(box, place):
    assert _path(box, place, write=True).verdict == "deny"
    assert _cmd(box, f"cp a.txt {place}").verdict == "deny"
    assert _cmd(box, f"python app.py > {place}").verdict == "deny"
    assert _cmd(box, f"chmod 777 {place} b.txt").verdict == "deny"


def test_a_path_outside_waits_for_her_yes_and_a_container_path_does_not(box, tmp_path):
    there = tmp_path / "elsewhere.txt"
    there.write_text("x", encoding="utf-8")
    assert _cmd(box, f"python app.py {there}").verdict == "ask"
    assert _cmd(box, f"cp a.txt {tmp_path / 'new.txt'}").verdict == "ask"
    assert _cmd(box, "python app.py /etc/hostname").verdict in ("ask", "allow")
    assert _cmd(box, "python app.py /no-such-place/in/a/container").verdict == "allow"


def test_devices_and_cmd_switch_shapes(box):
    assert _path(box, "/dev/sda").verdict == "deny" and _cmd(box, "python app.py /dev/sda").verdict == "deny"
    assert _cmd(box, "python app.py > /dev/null").verdict == "allow"
    assert _cmd(box, "mkdir /etc2x/x").verdict == "ask"
    assert _cmd(box, "rmdir /boot").verdict == "deny", "a folder here, never a cmd switch"
    assert _cmd(box, "find /").verdict == "deny"


def test_mounts_of_system_folders_the_whole_tmp_and_the_engine_are_refused(box):
    for source in ("/etc", "/usr/lib", "/tmp"):
        assert _cmd(box, f"docker run -v {source}:/x alpine").verdict == "deny", source
        assert Z.check(f"services:\n  a:\n    volumes:\n      - {source}:/x\n", folder=str(box[2]), kind="compose",
                       cwd=str(box[2]), zone=Z.zone_for(str(box[2]), state=str(box[1]))).verdict == "deny", source
    assert _path(box, "/var/run/docker.sock").verdict == "deny"
    tmpfs = "services:\n  a:\n    image: x\n    tmpfs:\n      - /tmp:size=64m,mode=1777\n"
    assert Z.check(tmpfs, folder=str(box[2]), kind="compose", cwd=str(box[2]),
                   zone=Z.zone_for(str(box[2]), state=str(box[1]))).verdict == "allow"


def test_tmp_is_the_trys_scratch_unless_a_link_leads_away(box, tmp_path):
    home, state, folder = box
    (home / ".ssh").mkdir()
    logs = state / "logs" / "toolsmith" / "mytry"
    (logs / "scratch").mkdir(parents=True)
    zone = Z.zone_for(str(folder), logs=str(logs), scratch=str(logs / "scratch"), state=str(state))
    check = lambda c: Z.check(c, folder=str(folder), kind="command", cwd=str(folder), zone=zone).verdict  # noqa: E731
    assert check("python app.py /tmp/d132-birds.csv") == "allow"
    link = f"/tmp/d132-link-{os.getpid()}"
    os.symlink(home / ".ssh", link)
    try:
        assert check(f"python app.py {link}/id_rsa") == "deny"
    finally:
        os.unlink(link)
    assert check(f"python app.py /tmp/../{home}/.ssh/id_rsa") == "deny"


@pytest.mark.parametrize("words", [
    "/proc/self/cwd/../../../machine.key", "/proc/self/cwd/machine.key", "/proc/self/root/etc/passwd",
    "/proc/thread-self/cwd/x", "/proc/self/fd/3", "/proc/1/cwd/x", "/proc/1/root/x", "/proc/self/task/1/cwd/x",
    "/proc/1/environ", "/proc/self", "//proc//self/./cwd/x", "/tmp/../proc/self/cwd/x", "/dev/fd/3/../../x"])
def test_a_path_through_a_running_program_is_no_place_the_check_can_read(box, words):
    assert _cmd(box, f"node arg.js {words}").verdict == "deny", words
    assert _path(box, words).verdict == "deny" and _path(box, words, write=True).verdict == "deny", words


def test_proc_through_a_link_or_a_climb_is_refused_and_plain_proc_reads_stay(box):
    folder = box[2]
    (folder / "jump").symlink_to("/proc/self/cwd")
    assert _path(box, folder / "jump" / "x").verdict == "deny"
    assert _cmd(box, "node arg.js jump/../../machine.key").verdict == "deny"
    assert _cmd(box, "node arg.js ../../../../../../../../../proc/self/cwd/x").verdict == "deny"
    assert _path(box, "/proc/cpuinfo").verdict == "ask"
    assert _cmd(box, "node arg.js /proc/cpuinfo").verdict == "ask"
    assert _path(box, "/proc/cpuinfo", write=True).verdict == "deny"
    (folder / "proc" / "self").mkdir(parents=True)
    assert _path(box, folder / "proc" / "self" / "x").verdict == "allow", "a folder of the try's own with that name"


def test_a_climb_out_of_a_link_is_read_from_where_the_link_lands(box):
    """``link/../machine.key`` opens the file beside the link's target: tidying ".." by text read it as the try's own."""
    root = data_root()
    (root / "other").mkdir(parents=True)
    (root / "machine.key").write_text("made up", encoding="utf-8")
    (box[2] / "link").symlink_to(root / "other")
    assert _path(box, box[2] / "link/../machine.key").verdict == "deny"
    assert _cmd(box, "node arg.js link/../machine.key").verdict == "deny"


def test_a_toolchain_is_never_the_whole_home_or_disk(box, monkeypatch):
    home = box[0]
    (home / "secret.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(Z, "_toolchains", lambda h: [h, "/", os.path.join(h, ".local", "bin")])
    zone = Z.zone_for(str(box[2]), state=str(box[1]))
    assert zone.classify(str(home / "secret.txt"))[0] == Z.OUTSIDE
    assert zone.classify("/opt/x")[0] == Z.OUTSIDE


@pytest.mark.parametrize("command", [
    "sudo cat a.txt", "sudo apt-get install x", "su -c id", "doas id", "pkexec id", "crontab cron.txt", "crontab -r",
    "systemctl restart ssh", "systemctl --user enable x", "launchctl load x.plist", "service ssh stop",
    "apt-get install x", "pacman -Syu", "dpkg -i x.deb", "brew install x", "reboot", "ufw disable",
    "pip install --target /opt/x requests", "python -m pip install --prefix=/opt/x requests",
    "pip install -t/opt/x requests"])
def test_a_change_to_the_computer_never_runs_in_a_try(box, command):
    got = _cmd(box, command)
    assert got.verdict == "deny" and "ask_owner" in got.line, got
    zone = Z.zone_for(str(box[2]), state=str(box[1]))
    assert Z.check(command, folder=str(box[2]), kind="command", cwd=str(box[2]), zone=zone, owner_step=True).verdict != "deny"


@pytest.mark.parametrize("command", ["crontab -l", "systemctl status ssh", "launchctl list", "apt list", "pip --version"])
def test_reading_the_computer_still_runs(box, command):
    assert _cmd(box, command).verdict != "deny"


@pytest.mark.parametrize("command", ["kill -9 4242", "kill -TERM 4242", "pkill python", "killall node", "kill -9 -1",
                                     "sudo kill 4242", "xargs kill"])
def test_stopping_another_program_is_refused(box, command):
    assert _cmd(box, command).verdict == "deny"


def test_a_mac_disk_ignores_case_and_so_does_the_check(box, monkeypatch):
    """Not run on a real Mac: this turns on the same fold. Apple's default disk format ignores letter case."""
    monkeypatch.setattr(Z, "_FOLD_CASE", True)
    home = box[0]
    (home / ".ssh").mkdir()
    assert _path(box, home / ".SSH" / "id_rsa").verdict == "deny"
    assert _path(box, home / "Library" / "KEYCHAINS" / "k").verdict == "deny"
    assert _path(box, "/ETC/x", write=True).verdict == "deny"


def test_a_trys_venv_cant_be_swapped_unnoticed(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    venv.create(folder / ".venv", with_pip=False, symlinks=True)
    assert os.path.islink(A.venv_python(folder)), "on Linux and Mac bin/python is a link to the base Python"
    record = A.venv_record(folder)
    place = A.Place(folder=folder, venv=record)
    assert record and A.decide(A.Request("python", ["a.py"]), place).kind == "run"

    cfg = folder / ".venv" / "pyvenv.cfg"
    before = cfg.read_bytes()
    cfg.write_bytes(before + b"include-system-site-packages = true\n")
    with pytest.raises(A.Refusal, match="changed after the Core made it"):
        A.decide(A.Request("python", ["a.py"]), place)
    cfg.write_bytes(before)

    py = A.venv_python(folder)
    target = os.readlink(py)
    py.unlink()
    other = tmp_path / "other"
    other.write_bytes(b"\x7fELF another program")
    py.symlink_to(other)
    with pytest.raises(A.Refusal, match="changed after the Core made it"):
        A.decide(A.Request("python", ["a.py"]), place)
    py.unlink()
    py.write_bytes(b"\x7fELF a copy in its place")
    with pytest.raises(A.Refusal, match="changed after the Core made it"):
        A.decide(A.Request("python", ["a.py"]), place)
    py.unlink()
    py.symlink_to(target)
    assert A.venv_record(folder) == record


def test_a_system_folder_cant_be_linked_as_a_package():
    from pathlib import Path

    from puffpai_core.packages.manager import link_problem
    assert "System folders" in link_problem(Path("/etc"), Path("/nonexistent-bot"))


def test_a_wizard_path_keeps_this_computers_slashes():
    from puffpai_core.wizard.fields import expand_path
    assert expand_path("/tmp/pt/state/kept") == "/tmp/pt/state/kept"
