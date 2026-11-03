"""D150: the window and the Desktop ask for the page ports again when the events stream comes back, and ``run.sh`` asks
docker for a terminal only when it has one. Her words, relayed: a package page on the network went blank until she reloaded.

The page code runs on a fake fetch (tests/js/d150.test.js); run.sh runs on a stand-in ``docker`` that records its arguments."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NODE, BASH = shutil.which("node"), shutil.which("bash")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_pages_ask_again_when_the_stream_comes_back():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "d150.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr


def _run_sh(tmp_path, stdin):
    """``run.sh chat`` on a stand-in docker: what it was asked to exec."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    docker = bin_dir / "docker"
    docker.write_text('#!/usr/bin/env bash\n[ "$1" = ps ] && echo buildbot-box\necho "$*" >> "$CALLS"\nexit 0\n', newline="\n")
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "CALLS": str(log), "DATA": str(tmp_path),
           "NAME": "buildbot-box"}
    script = (REPO / "scripts" / "container" / "run.sh").as_posix()
    done = subprocess.run([BASH, script, "chat"], stdin=stdin, env=env, capture_output=True, text=True, timeout=60)
    return done, log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_from_a_script_run_sh_asks_docker_for_no_terminal(tmp_path):
    done, calls = _run_sh(tmp_path, subprocess.DEVNULL)
    execs = [c for c in calls if c.startswith("exec ") and "puffpai" in c]
    assert execs, (done.stdout, done.stderr, calls)
    assert all(c.startswith("exec -i ") and " -t " not in c and "-it" not in c for c in execs), execs


def test_with_a_terminal_it_still_gets_one():
    """A real terminal can't be had here on every system, so this reads the one line that decides."""
    text = (REPO / "scripts" / "container" / "run.sh").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.startswith("in_box()"))
    assert '[ -t 0 ] && tty=(-t)' in line and 'docker exec -i "${tty[@]}"' in line
