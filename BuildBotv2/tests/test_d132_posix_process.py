"""D132: process control and the network tables on Linux and Mac. A wrapper that exited leaves its child seen and
ended, a zombie is not alive, the listen table and the TCP table read real sockets, a table that can't be read
is unknown, never empty. Every process here is one this test started."""

import os
import socket
import subprocess
import sys
import time

import pytest

from puffpai_core import devshell
from puffpai_core.steward import jobs, noshell, ports
from puffpai_core.zone3.bypass import BypassWatch, Conn, find_bypass, tcp_table

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the Linux and Mac side; Windows has job objects")

PY = sys.executable


def wait_for(check, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = check()
        if got:
            return got
        time.sleep(0.05)
    return check()


def spawn_py(tmp_path, code):
    return jobs.Job.spawn([PY, "-c", code], tmp_path, dict(os.environ), tmp_path / "out.log")


WRAPPER = ("import subprocess, sys; "
           "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
           "print(p.pid, flush=True)")


def child_of_wrapper(tmp_path, code=WRAPPER):
    job = spawn_py(tmp_path, code)
    log = tmp_path / "out.log"
    assert wait_for(lambda: log.read_text().strip()), "the wrapper said its child's number"
    return job, int(log.read_text().split()[0])


# -- the job ---------------------------------------------------------------------------------------------

def test_a_wrapper_that_exited_leaves_its_child_seen_and_ended_by_terminate(tmp_path):
    job, child = child_of_wrapper(tmp_path)
    try:
        job.proc.wait(timeout=10)                     # the wrapper is gone and reaped
        assert job.pids() == [child] and job.active() == 1 and job.alive()
        assert job.terminate(wait_s=10) is True
        assert not jobs.pid_alive(child) and job.pids() == [] and not job.alive()
    finally:
        job.terminate(wait_s=5)


def test_a_child_that_made_its_own_session_is_reached_while_its_parent_lives(tmp_path):
    code = ("import os, subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True); "
            "print(p.pid, flush=True); time.sleep(120)")
    job, child = child_of_wrapper(tmp_path, code)
    try:
        assert os.getsid(child) != job.pid and child in job.pids()
        assert job.terminate(wait_s=10) is True       # the child first, so it is still known through its parent
        assert not jobs.pid_alive(child)
    finally:
        job.terminate(wait_s=5)


def test_a_zombie_is_not_alive_not_even_the_leader_before_it_is_reaped(tmp_path):
    job = spawn_py(tmp_path, "pass")
    pid = job.pid
    # Without poll() the leader stays a zombie: read the table the way pids() does, with no reaping.
    def is_zombie():
        row = jobs._process_table().get(pid)
        return row is not None and row.zombie
    assert wait_for(is_zombie), "the leader ended and nobody has reaped it"
    assert not jobs.pid_alive(pid)
    assert job.pids() == [] and job.active() == 0 and not job.alive()
    assert job.terminate(wait_s=5) is True


def test_end_others_ends_all_but_what_is_kept_and_never_a_stranger(tmp_path):
    stranger = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"], start_new_session=True)
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            "print(p.pid, flush=True); time.sleep(120)")
    job, child = child_of_wrapper(tmp_path, code)
    try:
        assert job.end_others({job.pid}) == 1
        assert wait_for(lambda: job.pids() == [job.pid])
        assert not jobs.pid_alive(child) and jobs.pid_alive(job.pid)
        assert stranger.poll() is None
    finally:
        job.terminate(wait_s=5)
        stranger.kill()
        stranger.wait(timeout=10)


def test_a_number_given_to_another_program_after_the_job_ended_is_not_the_job(tmp_path):
    job = spawn_py(tmp_path, "pass")
    job.proc.wait(timeout=10)
    stranger = subprocess.Popen([PY, "-c", "import time; time.sleep(120)"], start_new_session=True)
    try:
        job.pid = stranger.pid                        # as if this number came back to another program
        assert job.pids() == [] and job.active() == 0
        assert job.terminate(wait_s=1) is True
        job.close()
        assert stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait(timeout=10)


def _sleeper():
    return subprocess.Popen([PY, "-c", "import time; time.sleep(120)"], start_new_session=True)


@pytest.mark.parametrize("reap", ["wait", "poll", "by_hand"])
def test_after_the_leader_is_reaped_its_number_is_never_matched_again(tmp_path, monkeypatch, reap):
    """The number wrapped: a daemon of another program has it as its session and group, and its own leader is
    gone. A real wrap takes minutes, so the table says it: a real process (the stranger) with the old number."""
    job = spawn_py(tmp_path, "pass")
    stranger = _sleeper()
    number = job.pid
    try:
        if reap == "by_hand":                         # a reap that never went through the leader's poll or wait
            os.waitpid(number, 0)
        elif reap == "wait":
            job.proc.wait(timeout=10)
        else:
            assert wait_for(lambda: job.proc.poll() is not None)
        real = jobs._process_table

        def wrapped():
            rows = real()
            rows[stranger.pid] = rows[stranger.pid]._replace(pgid=number, sid=number)
            return rows
        monkeypatch.setattr(jobs, "_process_table", wrapped)
        assert job.pids() == [] and job.active() == 0
        assert job.terminate(wait_s=1) is True
        job.close()
        assert job.end_others(frozenset()) == 0
        time.sleep(0.2)
        assert stranger.poll() is None, "the stranger was never signalled"
    finally:
        monkeypatch.undo()
        stranger.kill()
        stranger.wait(timeout=10)


def test_a_known_member_counts_only_while_it_is_the_same_process(tmp_path, monkeypatch):
    job, child = child_of_wrapper(tmp_path)
    try:
        job.proc.wait(timeout=10)                     # the last read before the reap remembered the child
        assert job.pids() == [child]
        real = jobs._process_table

        def came_back():
            rows = real()
            rows[child] = rows[child]._replace(start="another start time")
            return rows
        monkeypatch.setattr(jobs, "_process_table", came_back)
        assert job.pids() == []                       # its number, another program
        monkeypatch.undo()
        assert jobs.pid_alive(child)
    finally:
        monkeypatch.undo()
        try:
            os.kill(child, 9)
        except OSError:
            pass


def test_a_descendant_of_a_known_member_stays_seen_after_the_reap(tmp_path):
    code = ("import subprocess, sys; "
            "code = \"import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            "print(p.pid, flush=True); time.sleep(120)\"; "
            "subprocess.Popen([sys.executable, '-c', code])")
    job = spawn_py(tmp_path, code)
    log = tmp_path / "out.log"
    try:
        job.proc.wait(timeout=20)
        assert wait_for(lambda: log.read_text().strip())
        grandchild = int(log.read_text().split()[0])
        assert grandchild in job.pids() and len(job.pids()) == 2
        assert job.terminate(wait_s=10) is True and not jobs.pid_alive(grandchild)
    finally:
        job.terminate(wait_s=5)


def test_a_process_table_that_cant_be_read_is_never_an_empty_job(tmp_path, monkeypatch):
    job = spawn_py(tmp_path, "import time; time.sleep(120)")
    try:
        def broken():
            raise OSError(13, "denied")
        monkeypatch.setattr(jobs, "_process_table", broken)
        with pytest.raises(OSError):
            job.pids()
        assert job.alive()                            # what the leader says, not "nothing runs"
    finally:
        monkeypatch.undo()
        job.terminate(wait_s=5)


def test_the_mac_ps_lines_read_with_the_session_from_getsid():
    text = (f"  {os.getpid()}     1  {os.getpgrp()} 7f00aa Ss Sat Oct 31 21:51:02 2026\n"
            "  99999999     1   99999999 0 Z+\nnot a row\n")
    rows = jobs._ps_rows(text, darwin=True)
    assert rows[os.getpid()].sid == os.getsid(0) and not rows[os.getpid()].zombie
    assert rows[os.getpid()].start == "Sat Oct 31 21:51:02 2026"
    assert rows[99999999].zombie and rows[99999999].sid == -1
    assert 99999999 not in jobs._ps_rows("99999999 1 99999999 0x1 S\n", darwin=False)


# -- the listen table -------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_listen_table_finds_a_real_listener_with_its_owner(host):
    with socket.socket() as sock:
        sock.bind((host, 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        table = ports.listen_table()
        assert table is not None and (os.getpid(), host, port) in table
        assert (host, port) in ports.job_listeners([os.getpid()])
        assert ports.network_reachable([host]) == (host == "0.0.0.0")


def test_a_listener_of_a_real_job_is_found_through_its_pids(tmp_path):
    job = spawn_py(tmp_path, "import socket, time\ns = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(1)\n"
                             "print(s.getsockname()[1], flush=True); time.sleep(120)")
    try:
        log = tmp_path / "out.log"
        assert wait_for(lambda: log.read_text().strip())
        assert ports.job_listeners(job.pids()) == [("127.0.0.1", int(log.read_text()))]
    finally:
        job.terminate(wait_s=5)


def test_a_listen_table_that_cant_be_read_is_none_never_empty(monkeypatch):
    def broken(*_a, **_k):
        raise OSError(13, "denied")
    monkeypatch.setattr(ports, "tcp_rows", broken)
    monkeypatch.setattr(ports, "_ss_listen_table", lambda: None)
    assert ports.listen_table() is None and ports.job_listeners([os.getpid()]) is None
    monkeypatch.setattr(ports, "_ss_listen_table", lambda: [(1, "127.0.0.1", 80)])
    assert ports.listen_table() == [(1, "127.0.0.1", 80)]     # ss, when /proc can't be read


CANNED = """  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 111 1 0
   1: 0100007F:D431 22D8B85D:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 222 1 0
   2: 0100007F:D432 22D8B85D:0050 06 00000000:00000000 00:00000000 00000000  1000        0 0 1 0
"""
CANNED6 = """  sl  local_address                         remote_address                        st tx rx tr tm retr uid to inode
   0: 00000000000000000000000000000000:1F91 00000000000000000000000000000000:0000 0A 0:0 0:0 0 1000 0 333 1 0
   1: 00000000000000000000000001000000:1F92 00000000000000000000000000000000:0000 0A 0:0 0:0 0 1000 0 444 1 0
   2: 0000000000000000FFFF00000100007F:D433 0000000000000000FFFF000022D8B85D:01BB 01 0:0 0:0 0 1000 0 555 1 0
"""


@pytest.mark.skipif(sys.byteorder != "little", reason="the canned text is from a little-endian computer")
def test_proc_net_tcp_text_reads_in_both_families():
    assert ports._parse_proc_tcp(CANNED) == [
        (111, "127.0.0.1", 8080, "0.0.0.0", 0, "listen"),
        (222, "127.0.0.1", 54321, "93.184.216.34", 443, "established"),
        (0, "127.0.0.1", 54322, "93.184.216.34", 80, "time_wait")]
    assert ports._parse_proc_tcp(CANNED, listen_only=True) == [(111, "127.0.0.1", 8080, "0.0.0.0", 0, "listen")]
    assert ports._parse_proc_tcp(CANNED6) == [
        (333, "::", 8081, "::", 0, "listen"),
        (444, "::1", 8082, "::", 0, "listen"),
        (555, "::ffff:127.0.0.1", 54323, "::ffff:93.184.216.34", 443, "established")]
    with pytest.raises(ValueError):
        ports._parse_proc_tcp("header\n 0: short row\n")


@pytest.mark.skipif(sys.byteorder != "little", reason="the canned text is from a little-endian computer")
def test_ipv4_mapped_addresses_behave_as_the_watches_expect():
    (_i, local, _lp, remote, rport, state) = ports._parse_proc_tcp(CANNED6)[2]
    conn = Conn(7, local, 54323, remote, rport, state)
    assert find_bypass([7], [conn]) == [conn]                 # a mapped outside address counts
    home = Conn(7, local, 54323, "::ffff:127.0.0.1", 8421, "established")
    assert find_bypass([7], [home]) == []                     # a mapped loopback does not
    assert not ports.network_reachable(["::ffff:127.0.0.1"]) and ports.network_reachable(["::ffff:192.168.1.5"])


LSOF = """p501
f7
n127.0.0.1:18500
TST=LISTEN
TQR=0
TQS=0
f8
n*:18501
TST=LISTEN
f9
n[::1]:18502
TST=LISTEN
f10
n192.168.1.5:51000->93.184.216.34:443
TST=ESTABLISHED
p502
f3
n[::1]:51001->[::1]:18500
TST=FIN_WAIT_2
f4
n*:*
TST=CLOSED
"""


def test_the_mac_lsof_lines_read_as_rows():
    assert ports._parse_lsof(LSOF) == [
        (501, "127.0.0.1", 18500, "0.0.0.0", 0, "listen"),
        (501, "0.0.0.0", 18501, "0.0.0.0", 0, "listen"),
        (501, "::1", 18502, "0.0.0.0", 0, "listen"),
        (501, "192.168.1.5", 51000, "93.184.216.34", 443, "established"),
        (502, "::1", 51001, "::1", 18500, "fin_wait2")]


# -- the TCP table and the watch ---------------------------------------------------------------------------

def _own_lan_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:   # no packet goes out
            sock.connect(("10.255.255.255", 9))
            addr = sock.getsockname()[0]
    except OSError:
        return None
    return None if addr.startswith("127.") else addr


def test_tcp_table_sees_a_real_connection_past_loopback():
    addr = _own_lan_address()
    if addr is None:
        pytest.skip("this computer has no address beyond loopback (the canned text covers the reading)")
    with socket.socket() as server:
        server.bind(("0.0.0.0", 0))
        server.listen(1)
        with socket.create_connection((addr, server.getsockname()[1]), timeout=5) as client:
            accepted, _ = server.accept()
            with accepted:
                port = client.getsockname()[1]
                conns = tcp_table()
                mine = [c for c in conns if c.pid == os.getpid() and c.local_port == port]
                assert mine and mine[0].state == "established" and mine[0].remote == addr
                caught = find_bypass([os.getpid()], conns)
                assert any(c.local_port == port for c in caught)
                # what arrived at a port this process listens on is inbound, and does not count
                assert not any(c.local_port == server.getsockname()[1] for c in caught)


class _Steward:
    def running(self):
        return ["a"]

    def pids(self, _id):
        return []

    def stop(self, *_a):
        return None


def test_the_watch_runs_here_and_a_table_that_cant_be_read_says_so(monkeypatch):
    watch = BypassWatch(_Steward())
    assert watch.problem == ""
    assert watch.check_once() == [] and watch.problem == ""

    def broken():
        raise OSError(13, "denied")
    cards = []
    watch = BypassWatch(_Steward(), table=broken, on_card=cards.append)
    assert watch.check_once() == [] and watch.problem == "no_tcp_table"
    assert "Windows" not in cards[0]["line"] and "wouldn't list its connections" in cards[0]["line"]


def test_an_os_with_no_table_says_so(monkeypatch):
    monkeypatch.setattr(ports, "has_tcp_table", lambda: False)
    assert BypassWatch(_Steward()).problem == "not_windows"


# -- node and npm ------------------------------------------------------------------------------------------

def _file(path, text="// fake\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def _link(path, target):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)


def test_debian_layout(tmp_path, monkeypatch):
    node = _file(tmp_path / "usr/bin/node", "#!/bin/sh\n")
    cli = _file(tmp_path / "usr/share/nodejs/npm/bin/npm-cli.js")
    _link(tmp_path / "usr/bin/npm", "../share/nodejs/npm/bin/npm-cli.js")
    monkeypatch.setenv("PATH", str(tmp_path / "usr/bin"))
    assert noshell.node_exe() == os.path.realpath(node)
    real = os.path.realpath(cli)
    assert noshell.resolve(["npm", "install"]) == [os.path.realpath(node), real, "install"]
    assert noshell.resolve([str(tmp_path / "usr/bin/npm"), "start"]) == [os.path.realpath(node), real, "start"]


def test_nvm_layout(tmp_path, monkeypatch):
    bin_dir = tmp_path / ".nvm/versions/node/v22/bin"
    node = _file(bin_dir / "node", "#!/bin/sh\n")
    cli = _file(bin_dir.parent / "lib/node_modules/npm/bin/npx-cli.js")
    _link(bin_dir / "npx", "../lib/node_modules/npm/bin/npx-cli.js")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert noshell.resolve(["npx", "serve"]) == [os.path.realpath(node), os.path.realpath(cli), "serve"]


def test_homebrew_layout_the_real_node_is_in_its_cellar(tmp_path, monkeypatch):
    prefix = tmp_path / "homebrew"
    _file(prefix / "Cellar/node/24/bin/node", "#!/bin/sh\n")
    _link(prefix / "bin/node", "../Cellar/node/24/bin/node")
    cli = _file(prefix / "lib/node_modules/npm/bin/npm-cli.js")
    _link(prefix / "bin/npm", "../lib/node_modules/npm/bin/npm-cli.js")
    monkeypatch.setenv("PATH", str(prefix / "bin"))
    node = noshell.node_exe()
    assert node == os.path.join(os.path.realpath(prefix / "bin"), "node")
    assert noshell.resolve(["npm", "ci"]) == [node, os.path.realpath(cli), "ci"]


def test_a_script_shim_that_hides_the_real_npm_is_said_plainly(tmp_path, monkeypatch):
    shims = tmp_path / "shims"
    _file(shims / "node", "#!/bin/sh\n")
    _file(shims / "npm", "#!/bin/sh\n")
    monkeypatch.setenv("PATH", str(shims))
    with pytest.raises(noshell.NoShell, match="needs Node.js"):
        noshell.resolve(["npm", "install"])


@pytest.mark.skipif(not noshell.npm_cli(noshell.node_exe(), "npm"), reason="no Node.js with npm here")
def test_the_real_npm_on_this_computer_runs_with_no_shell(tmp_path):
    argv = noshell.resolve(["npm", "--version"])
    assert argv[0] == noshell.node_exe() and argv[1].endswith("npm-cli.js")
    env = dict(os.environ, HOME=str(tmp_path), npm_config_cache=str(tmp_path / "npm-cache"))
    got = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert got.returncode == 0 and got.stdout.strip()[0].isdigit()


# -- the Developer page's command line ---------------------------------------------------------------------

@pytest.fixture
def shells(tmp_path):
    mgr = devshell.Shells(tmp_path, reaper=False, env_source=lambda: dict(os.environ, PUFFPAI_X="no", BASH_ENV="/x"))
    yield mgr
    mgr.close_all("test_end")


def _text(sh, stream="out"):
    return "".join(e.get("text") or "" for _s, e in sh.since(0) if e.get("stream") == stream)


def _ran(sh):
    return wait_for(lambda: not sh.busy, 15)


@pytest.mark.parametrize("kind", devshell.SHELLS)
def test_a_command_line_runs_and_keeps_its_folder(shells, tmp_path, kind):
    if not os.path.isfile(devshell.BASH if kind == "bash" else devshell.SH):
        pytest.skip(f"no {kind} here")
    (tmp_path / "with space").mkdir()
    sh = shells.open(kind, "me")
    shells.line(sh, "echo hello")
    assert _ran(sh) and wait_for(lambda: "hello\n" in _text(sh))
    shells.line(sh, 'cd "with space"')
    assert _ran(sh) and sh.cwd == str(tmp_path / "with space")
    shells.line(sh, "false")
    assert _ran(sh) and wait_for(lambda: "(exit code 1)" in _text(sh))
    shells.line(sh, "echo $PUFFPAI_X-${BASH_ENV}-end")
    assert _ran(sh) and wait_for(lambda: "--end\n" in _text(sh))


def test_ctrl_c_ends_what_runs_and_keeps_the_shell_and_close_ends_all(shells):
    sh = shells.open(devshell.DEFAULT_SHELL, "me")
    assert sh.job.pids() == [sh.job.pid]
    shells.line(sh, f'"{PY}" -c "import time; time.sleep(120)"')
    assert wait_for(lambda: len(sh.job.pids()) == 2)
    assert shells.ctrl_c(sh) == "stopped"
    shells.line(sh, "echo still here")
    assert _ran(sh) and wait_for(lambda: "still here" in _text(sh))
    shells.line(sh, f'"{PY}" -c "import time; time.sleep(120)" &')
    assert wait_for(lambda: len(sh.job.pids()) == 2)
    left = sh.job.pids()
    assert shells.close(sh.id)
    assert not any(jobs.pid_alive(p) for p in left)


def test_shell_names_here():
    assert devshell.SHELLS == ("bash", "sh") and devshell.DEFAULT_SHELL in devshell.SHELLS
    assert devshell.shell_argv("sh") == [devshell.SH]
    with pytest.raises(devshell.StartFailed):
        devshell.shell_argv("powershell")
