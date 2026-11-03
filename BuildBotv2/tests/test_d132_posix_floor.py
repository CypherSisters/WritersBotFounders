"""D132: the floor rules on Linux and Mac, with real files and real links. Skipped on Windows."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import sys
import time
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
    assert _cmd(box, f"python app.py {shlex.quote(str(root / 'machine.key'))}").verdict == "deny"
    assert _path(box, root.parent).verdict == "deny", "a folder that holds them"


def _bot_folder_with_a_space(box, monkeypatch):
    """Where a Mac keeps it, on any computer: PUFFPAI_HOME moves it there."""
    root = box[0] / "Library" / "Application Support" / "PuffPAI"
    root.mkdir(parents=True)
    (root / "machine.key").write_text("made up", encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_HOME", str(root))
    return root


def test_a_quoted_path_with_a_space_is_her_keys_too(box, monkeypatch):
    key = _bot_folder_with_a_space(box, monkeypatch) / "machine.key"
    keys = Z.refused_line(Z.KEYS)
    for command in (f"python app.py '{key}'", f'python app.py "{key}"', f'python app.py --key="{key}"',
                    f"cp a.txt '{key}'", f"python app.py {shlex.quote(str(key))}"):
        got = _cmd(box, command)
        assert got.verdict == "deny" and got.line == keys, command
    got = _path(box, key)
    assert got.verdict == "deny" and got.line == keys


def test_her_keys_in_another_letter_case_are_her_keys_on_a_mac(box, monkeypatch):
    """Not run on a real Mac: this turns on the same fold. Apple's default disk format ignores letter case."""
    _bot_folder_with_a_space(box, monkeypatch)
    monkeypatch.setattr(Z, "_FOLD_CASE", True)
    home, keys = box[0], Z.refused_line(Z.KEYS)
    for place in (home / "library" / "application support" / "puffpai" / "MACHINE.KEY",
                  home / "LIBRARY" / "Application Support" / "PUFFPAI" / "other" / "VAULT" / "x"):
        for got in (_path(box, place), _path(box, place, write=True), _cmd(box, f"python app.py '{place}'")):
            assert got.verdict == "deny" and got.line == keys, place


def test_a_mac_disk_ignores_case_in_the_folder_checks_too(box, monkeypatch):
    """Not run on a real Mac, as above. A knowledge folder in another spelling of a place she may not use."""
    from puffpai_core.zone1 import folders, knowledge_folder as kf
    home, state = box[0], box[1]
    (home / ".ssh").mkdir()
    if sys.platform != "darwin":                                      # on a disk that reads case they are two folders
        assert kf.problem(state, home / ".SSH") == ""
    monkeypatch.setattr(folders, "_FOLD_CASE", True)
    assert kf.problem(state, home / ".SSH") == kf.SECRETS
    # A Mac's /etc is a link to /private/etc, so there it is refused as a link. Refused either way.
    assert kf.problem(state, "/ETC/books") in ((kf.SYSTEM, kf.LINK) if sys.platform == "darwin" else (kf.SYSTEM,))


def test_the_xdg_folders_she_moved_are_refused_too(box, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "shared"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    # On a Mac the bot's folder ignores XDG_DATA_HOME (Library/Application Support), so that key is not there.
    for place in ("cfg/gcloud/c", "cfg/google-chrome/p", "shared/keyrings/k", "run/agent",
                  *(["shared/puffpai/machine.key"] if sys.platform != "darwin" else [])):
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
    # Up to the top on both: a Mac's /tmp is /private/tmp, one folder deeper.
    assert check(f"python app.py /tmp/../../{home}/.ssh/id_rsa") == "deny"


@pytest.mark.parametrize("words", [
    "/proc/self/cwd/../../../machine.key", "/proc/self/cwd/machine.key", "/proc/self/root/etc/passwd",
    "/proc/thread-self/cwd/x", "/proc/self/fd/3", "/proc/1/cwd/x", "/proc/1/root/x", "/proc/self/task/1/cwd/x",
    "/proc/1/environ", "/proc/self", "//proc//self/./cwd/x", "/tmp/../../proc/self/cwd/x", "/dev/fd/3/../../x"])
def test_a_path_through_a_running_program_is_no_place_the_check_can_read(box, words):
    assert _cmd(box, f"node arg.js {words}").verdict == "deny", words
    assert _path(box, words).verdict == "deny" and _path(box, words, write=True).verdict == "deny", words


def test_proc_through_a_link_or_a_climb_is_refused_and_plain_proc_reads_stay(box):
    folder = box[2]
    (folder / "jump").symlink_to("/proc/self/cwd")
    assert _path(box, folder / "jump" / "x").verdict == "deny"
    assert _cmd(box, "node arg.js jump/../../machine.key").verdict == "deny"
    assert _cmd(box, "node arg.js " + "../" * 40 + "proc/self/cwd/x").verdict == "deny"     # to the top from any temp folder
    assert _path(box, "/proc/cpuinfo").verdict == "ask"
    # A Mac has no /proc: a path outside that names nothing here is taken as one inside a container.
    assert _cmd(box, "node arg.js /proc/cpuinfo").verdict == ("ask" if os.path.exists("/proc/cpuinfo") else "allow")
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


def _other_app(box):
    other = box[1] / "packages" / "other"
    other.mkdir()
    (other / "a.py").write_text("made up", encoding="utf-8")
    return other


@pytest.mark.parametrize("form", ["/{}", "//{}", "///{}", "/./{}", "//./{}", "/no-such-place/../{}"])
def test_every_spelling_of_a_path_is_the_place_it_opens(box, form):
    """Linux and Mac's equal of Windows' odd forms: the kernel opens each of these, so the check reads each the same."""
    home, _state, _folder = box
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("made up", encoding="utf-8")
    for place in (home / ".ssh" / "id_rsa", _other_app(box) / "a.py"):
        target = form.format(str(place).lstrip("/"))
        if "no-such-place" not in form:
            assert os.path.exists(target), "the kernel really opens this"
        for write in (False, True):
            assert _path(box, target, write=write).verdict == "deny", target
        assert _cmd(box, f'node arg.js "{target}"').verdict == "deny", target
    for form_of_etc in (form.format("etc/x"),):
        assert _path(box, form_of_etc, write=True).verdict == "deny", form_of_etc


@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\", "\\??\\", "/\\?\\", "\\\\localhost\\C$"])
def test_a_windows_odd_form_opens_no_other_place_here(box, prefix):
    """On Linux and Mac a backslash is a letter of a file name: such a word is one name in the try, with a real file to
    prove it. Nothing it spells out is reached, so it needs no refusal of its own."""
    other = _other_app(box)
    word = prefix + str(other / "a.py")
    assert not os.path.exists(os.path.join(str(box[2]), word)), "no file of that name, and not the other app's"
    # One name in the try, or (with a slash first) a folder at the top of the disk that is not there: never the other app.
    assert _path(box, os.path.join(str(box[2]), word)).verdict == ("ask" if word.startswith("/") else "allow")
    assert _cmd(box, f'node arg.js "{word}"').verdict != "deny"


def test_two_slashes_name_no_share_here(box):
    """``//host/share`` is the plain folder ``/host/share`` on Linux: nothing goes to a host. It is judged as a place."""
    for words in ("//nas-nobody/share/a.txt", "//192.168.1.5/c$/x"):
        assert _path(box, words, write=True).verdict == "ask", words
        assert _path(box, words).verdict == "ask", words
    assert _path(box, "//etc/x", write=True).verdict == "deny"
    assert _cmd(box, "cp a.txt //etc/x").verdict == "deny"


def test_a_colon_or_a_trailing_dot_is_a_letter_of_a_name_and_hooks_stay_refused(box):
    """No NTFS streams here and no dot dropped: these are plain names. A write into site-packages is refused whatever
    the name, and only an exact ``.pth`` runs at Python's start."""
    folder = box[2]
    for name in ("a.txt:hidden", "q.pth::$DATA", "a.py.", "a.py "):
        assert _path(box, folder / name).verdict == "allow", name
        assert _path(box, folder / name, write=True).verdict == "allow", name
    site = folder / SITE
    site.mkdir(parents=True)
    for name in ("q.pth::$DATA", "a.pth:x", "a.pth.", "a.pth ", "sitecustomize.py.", "plain.py"):
        assert _path(box, site / name, write=True).verdict == "deny", name
    assert _path(box, site / "A.PTH").verdict == "deny", "a Mac reads it as a.pth, and refusing it costs nothing"


def test_a_link_in_the_try_reads_as_where_it_lands(box):
    """Linux and Mac's equal of Windows' junction cases: the other app, a hook, site-packages, and a link only NAMED so."""
    folder = box[2]
    other = _other_app(box)
    (folder / "jump").symlink_to(other)
    assert _path(box, folder / "jump" / "a.py").verdict == "deny"
    assert _path(box, folder / "jump" / "a.py", write=True).verdict == "deny"
    assert _cmd(box, "node arg.js jump/a.py").verdict == "deny"
    site = folder / SITE
    site.mkdir(parents=True)
    (folder / "sitejump").symlink_to(site)
    assert _path(box, folder / "sitejump" / "a.pth").verdict == "deny"
    assert _path(box, folder / "sitejump" / "plain.py", write=True).verdict == "deny"
    assert _path(box, folder / "sitejump" / "plain.py").verdict == "allow", "reading it is fine"
    (folder / "plain").mkdir()
    (folder / "v2" / "lib").mkdir(parents=True)
    (folder / "v2" / "lib" / "site-packages").symlink_to(folder / "plain")
    assert _path(box, folder / "v2" / "lib" / "site-packages" / "a.pth").verdict == "deny", "the path's own words count"


def test_what_is_already_in_the_real_tmp_waits_for_her_yes(box):
    """Nothing swaps the real /tmp for the try's scratch folder here: what is there is another program's, like the
    ssh-agent's socket. A new name is the try's own. Asked with real files."""
    _home, state, folder = box
    logs = state / "logs" / "toolsmith" / "mytry"
    (logs / "scratch").mkdir(parents=True)
    zone = Z.zone_for(str(folder), logs=str(logs), scratch=str(logs / "scratch"), state=str(state))
    check = lambda c: Z.check(c, folder=str(folder), kind="command", cwd=str(folder), zone=zone).verdict  # noqa: E731
    named = f"/tmp/d132-another-programs-{os.getpid()}"
    assert check(f"python app.py {named}") == "allow", "nothing there yet: the try's own"
    os.mkdir(named)
    try:
        open(os.path.join(named, "agent.1"), "w", encoding="utf-8").close()
        assert check(f"python app.py {named}/agent.1") == "ask"
        assert check(f"python app.py {named}") == "ask"
        assert check(f"cp a.txt {named}/agent.1") == "ask"
        assert check(f"python app.py {named}/not-there-yet") == "allow"
    finally:
        os.unlink(os.path.join(named, "agent.1"))
        os.rmdir(named)
    assert check("python app.py /tmp") == "allow", "the name itself is the try's scratch"


@pytest.mark.parametrize("command", ["ls ../oth*", "ls ../*", "cat ../../set*/*"])
def test_a_wildcard_that_can_climb_is_judged_where_it_lands(box, command):
    _other_app(box)
    assert _cmd(box, command).verdict == "deny", command


@pytest.mark.parametrize("command", ["ls .git*", "cat .env*", "ls .[a-z]*", "ls *.py", "python serve.py '.*'",
                                     "python -m pytest -k .*", "node .*/x"])
def test_a_dot_wildcard_that_stays_in_the_try_still_runs(box, command):
    assert _cmd(box, command).verdict == "allow", command


@pytest.fixture
def began(box):
    """A try that has begun, and its check. ``/tmp`` names of this test's own, removed after it, older ones first."""
    _home, state, folder = box
    logs = state / "logs" / "toolsmith" / "mytry"
    (logs / "scratch").mkdir(parents=True)
    old = f"/tmp/d132-old-{os.getpid()}"
    new = f"/tmp/d132-new-{os.getpid()}"
    os.mkdir(old)
    old_file = os.path.join(old, "agent.1")
    open(old_file, "w", encoding="utf-8").close()
    made = [old_file, old]
    time.sleep(0.05)
    Z.mark_began(logs / "scratch")
    time.sleep(0.05)
    zone = Z.zone_for(str(folder), logs=str(logs), scratch=str(logs / "scratch"), state=str(state))

    def check(command):
        return Z.check(command, folder=str(folder), kind="command", cwd=str(folder), zone=zone).verdict
    yield check, old, new, made
    for path in made:                                  # only the names this test made
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        elif os.path.lexists(path):
            os.unlink(path)


def test_a_file_made_after_the_try_began_stays_scratch(began):
    check, _old, new, made = began
    file = new + ".csv"
    made.append(file)
    open(file, "w", encoding="utf-8").close()
    assert check(f"python app.py {file}") == "allow"
    assert check(f"cp a.txt {file}") == "allow"
    os.mkdir(new)
    made.append(new)
    inner = os.path.join(new, "out.txt")
    made.append(inner)
    open(inner, "w", encoding="utf-8").close()
    assert check(f"python app.py {new}") == "allow"
    assert check(f"python app.py {inner}") == "allow", "her card is only for what was already there"
    assert check(f"python app.py {new}/not-there-yet") == "allow"


def test_a_file_older_than_the_try_asks(began):
    check, old, _new, _made = began
    assert check(f"python app.py {old}") == "ask"
    assert check(f"python app.py {old}/agent.1") == "ask"
    assert check(f"cp a.txt {old}/agent.1") == "ask"
    assert check(f"python app.py {old}/not-there-yet") == "allow", "nothing there yet: the try's own"


def test_an_old_file_with_a_new_time_on_its_inode_or_moved_in_asks(began):
    check, old, new, made = began
    moved = new + ".moved"
    made.insert(0, moved)
    os.utime(os.path.join(old, "agent.1"), (1_000_000_000, 1_000_000_000))
    os.rename(os.path.join(old, "agent.1"), moved)        # its ctime is new now, its mtime is not
    assert check(f"python app.py {moved}") == "ask"


def test_a_socket_from_before_the_try_asks_and_its_own_later_one_does_not(box, tmp_path):
    _home, state, folder = box
    logs = state / "logs" / "toolsmith" / "mytry"
    (logs / "scratch").mkdir(parents=True)
    older, later = f"/tmp/d132-s1-{os.getpid()}", f"/tmp/d132-s2-{os.getpid()}"
    with socket.socket(socket.AF_UNIX) as one:
        one.bind(older)
        try:
            time.sleep(0.05)
            Z.mark_began(logs / "scratch")
            time.sleep(0.05)
            with socket.socket(socket.AF_UNIX) as two:
                two.bind(later)
                try:
                    zone = Z.zone_for(str(folder), logs=str(logs), scratch=str(logs / "scratch"), state=str(state))
                    check = lambda p: Z.check(f"python app.py {p}", folder=str(folder), kind="command",  # noqa: E731
                                              cwd=str(folder), zone=zone).verdict
                    assert check(older) == "ask"
                    assert check(later) == "allow"
                finally:
                    os.unlink(later)
        finally:
            os.unlink(older)


def test_another_accounts_file_asks_however_new(began, monkeypatch):
    check, _old, new, made = began
    file = new + ".theirs"
    made.append(file)
    open(file, "w", encoding="utf-8").close()
    assert check(f"python app.py {file}") == "allow"
    mine = os.getuid()
    monkeypatch.setattr(Z.os, "getuid", lambda: mine + 1)        # the same as the file being another account's
    assert check(f"python app.py {file}") == "ask"


def test_a_link_the_try_made_is_read_as_where_it_lands(began, box):
    home = box[0]
    check, old, new, made = began
    (home / ".ssh").mkdir()
    (home / "plain.txt").write_text("x", encoding="utf-8")
    for name, target, want in (("-a", os.path.join(old, "agent.1"), "ask"),   # to an older file in /tmp
                               ("-b", home / ".ssh" / "id_rsa", "deny"),       # out of /tmp, to her keys
                               ("-c", home / "plain.txt", "ask")):             # out of /tmp, to a plain file of hers
        link = new + name
        made.append(link)
        os.symlink(target, link)
        assert check(f"python app.py {link}") == want, name
    own = new + ".own"
    made.append(own)
    open(own, "w", encoding="utf-8").close()
    link = new + "-d"
    made.append(link)
    os.symlink(own, link)
    assert check(f"python app.py {link}") == "allow", "to its own file"


def test_with_no_start_on_record_what_is_there_asks(box):
    _home, state, folder = box
    logs = state / "logs" / "toolsmith" / "mytry"
    (logs / "scratch").mkdir(parents=True)
    zone = Z.zone_for(str(folder), logs=str(logs), scratch=str(logs / "scratch"), state=str(state))
    file = f"/tmp/d132-nomark-{os.getpid()}"
    open(file, "w", encoding="utf-8").close()
    try:
        assert Z.check(f"python app.py {file}", folder=str(folder), kind="command", cwd=str(folder), zone=zone).verdict == "ask"
    finally:
        os.unlink(file)


def test_a_real_try_records_its_start_and_its_own_tmp_file_gets_no_card(tmp_path, state):
    from test_d61_allowlist import HELLO, _checked
    from toolsmith_fakes import World
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    real = Ports.zone_check
    w.ports.zone_check = lambda *a, **k: real(w.ports, *a, **k)
    older = f"/tmp/d132-before-{os.getpid()}"
    mine = f"/tmp/d132-mine-{os.getpid()}"
    open(older, "w", encoding="utf-8").close()
    time.sleep(0.05)
    try:
        _b, _link, _try_id, rec = _checked(w)
        assert (rec.logs / Z.BEGAN_FILE).is_file()
        time.sleep(0.05)
        open(mine, "w", encoding="utf-8").close()
        assert w.host.trials._zone(rec, f"python app.py {mine}", "command").verdict == "allow"
        assert w.host.trials._zone(rec, f"python app.py {older}", "command").verdict == "ask"
    finally:
        w.host.stop(timeout=10)
        for path in (older, mine):
            if os.path.lexists(path):
                os.unlink(path)


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
    # A Mac's /etc is a link to /private/etc: refused as a link, and the real one as a system folder (below).
    assert ("link to somewhere else" if os.path.islink("/etc") else "System folders") in link_problem(
        Path("/etc"), Path("/nonexistent-bot"))


def test_a_macs_real_etc_is_a_system_folder(box, monkeypatch):
    from pathlib import Path

    from puffpai_core.packages import manager
    from puffpai_core.packages.manager import link_problem
    from puffpai_core.zone1 import knowledge_folder as kf
    monkeypatch.setattr(Path, "is_dir", lambda self: True)            # no /private/etc off a Mac: only the words are read
    monkeypatch.setattr(manager.fsutil, "is_link", lambda _p: False)
    assert "System folders" in link_problem(Path("/private/etc"), Path("/nonexistent-bot"))
    assert "System folders" in link_problem(Path("/private/etc/ssh"), Path("/nonexistent-bot"))
    monkeypatch.undo()
    assert kf.problem(box[1], "/private/etc") == kf.SYSTEM


def test_a_wizard_path_keeps_this_computers_slashes():
    from puffpai_core.wizard.fields import expand_path
    assert expand_path("/tmp/pt/state/kept") == "/tmp/pt/state/kept"
