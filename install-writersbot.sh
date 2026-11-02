#!/usr/bin/env bash
# WritersBotFounders on a Jetson Orin: the same recipe as the box it was built on. One container, uid 1000, on Docker's
# bridge network, 8 GB of memory and 4 CPUs, using the Ollama that already runs on the Orin. No sudo in the container,
# no docker.sock, no --privileged, no host networking. Removing the container and the data folder removes everything.
# Run it as yourself, with no sudo. It asks for your password only if it has to change something outside your home.
#
#   ./install-writersbot.sh                      build the image, install, then setup (asks about the model, the vault, the Bridge, Toolsmith)
#   ./install-writersbot.sh --no-setup           build and install only; run setup later with: ./install-writersbot.sh setup
#   ./install-writersbot.sh setup|start|stop|pair|chat     the bot's everyday commands, same as the box it came from
#   ./install-writersbot.sh update               bring the bot's Core, window and own apps up to this copy, each app with its Yes card; its data is not touched
#   ./install-writersbot.sh --pictures           also set up the picture program on this Orin (install-pictures.sh)
#
# Running it again is safe: what is already done is skipped.
# Settings you may change, as environment variables: DATA (where the bot keeps everything, default $HOME/puffpai/writersbot),
# LAN (this Orin's home-network address; found by itself when empty), MEMORY (8g), CPUS (4), OLLAMA (Docker's bridge address, port 11434).
set -euo pipefail

# Not as root: Docker, the picture program and everything in your home folder belong to you.
if [ "$(id -u)" = 0 ]; then
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
        echo "This script does not need sudo. Going on as $SUDO_USER."
        exec sudo -u "$SUDO_USER" -H ${DATA:+env "DATA=$DATA"} bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
    fi
    echo "Do not run this as root. Log in as yourself and run it again, with no sudo."
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NAME="${NAME:-writersbot-box}"
export BOT="${BOT:-writersbot}"
export IMAGE="${IMAGE:-puffpai-box}"
export MEMORY="${MEMORY:-8g}"
export CPUS="${CPUS:-4}"
export CODE="$HERE/BuildBotv2"
RUN="$CODE/scripts/container/run.sh"
MODEL_WANTED="qwen3:30b-a3b"

# The folder this container already keeps the bot in, if there is such a container.
mount_of() { docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data/bot"}}{{.Source}}{{end}}{{end}}' "$NAME" 2>/dev/null || true; }

verb="${1:-install}"
case "$verb" in
    setup|start|stop|pair|chat|update) DATA="${DATA:-$(mount_of)}" ;;   # so a custom DATA keeps working
    install|--no-setup|--pictures) ;;
    -h|--help) sed -n 2,15p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown: $verb"; sed -n 2,15p "${BASH_SOURCE[0]}"; exit 1 ;;
esac
DATA="${DATA:-$HOME/puffpai/writersbot}"
export DATA="${DATA%/}"

# The host as a container sees it.
GATEWAY="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
GATEWAY="${GATEWAY:-172.17.0.1}"
export OLLAMA="${OLLAMA:-$GATEWAY:11434}"

# The address this computer really uses to go out. hostname -I gives every network, in no promised order.
if [ -z "${LAN:-}" ]; then
    LAN="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' || true)"
    if [ -z "$LAN" ]; then LAN="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"; fi
fi
export LAN

say() { printf '\n== %s\n' "$*"; }
ollama_answers() { curl -fsS --max-time 3 "http://$1/api/version" >/dev/null 2>&1; }

wide_said=0
wide_once() {
    if [ "$wide_said" = 0 ]; then echo "  This makes Ollama reachable on your home network with no password."; fi
    wide_said=1
}

ollama_listen_wider() {
    local conf=/etc/systemd/system/ollama.service.d/writersbot.conf i
    echo "  Ollama only listens on this computer, and the container needs it to listen wider. Changing that now; it may ask for your password."
    wide_once
    if sudo mkdir -p "${conf%/*}" && printf '[Service]\nEnvironment="OLLAMA_HOST=0.0.0.0:11434"\n' | sudo tee "$conf" >/dev/null \
        && sudo systemctl daemon-reload && sudo systemctl restart ollama; then
        for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
            if ollama_answers "$OLLAMA"; then return 0; fi
            sleep 2
        done
    fi
    return 1
}

# Tested from this computer, at the address the container uses, so nobody waits for a build that can't work. A firewall on
# Docker's network passes this test; only run.sh up, from inside the container after the build, can catch that (box_up).
check_ollama() {
    local has_unit=0 loopback=0
    if ollama_answers "$OLLAMA"; then return 0; fi
    if systemctl cat ollama.service >/dev/null 2>&1; then has_unit=1; fi
    if ollama_answers 127.0.0.1:11434; then loopback=1; fi
    if [ "$has_unit" = 0 ] && [ "$loopback" = 0 ] && ! command -v ollama >/dev/null; then
        echo "  Ollama is not on this computer, and the bot needs it to think. Nothing was built. To install it:"
        echo "    curl -fsSL https://ollama.com/install.sh | sh"
        echo "  Then run ./install-writersbot.sh again."
        exit 1
    fi
    if [ "${OLLAMA%%:*}" != "$GATEWAY" ]; then
        echo "  OLLAMA is set to $OLLAMA and nothing answers there. Nothing was built. Fix that address, or leave OLLAMA unset."
        exit 1
    fi
    if [ "$has_unit" = 1 ] && [ "$loopback" = 1 ]; then
        if ollama_listen_wider; then return 0; fi
        echo "  Ollama still does not answer at $OLLAMA. Nothing was built."
    else
        echo "  Ollama does not answer at $OLLAMA, which is where the container asks it. Nothing was built."
    fi
    wide_once
    echo "  Do this, then run ./install-writersbot.sh again:"
    if [ "$has_unit" = 1 ]; then
        echo "    sudo mkdir -p /etc/systemd/system/ollama.service.d"
        echo "    printf '[Service]\\nEnvironment=\"OLLAMA_HOST=0.0.0.0:11434\"\\n' | sudo tee /etc/systemd/system/ollama.service.d/writersbot.conf"
        echo "    sudo systemctl daemon-reload"
        echo "    sudo systemctl restart ollama"
    else
        echo "    Stop any Ollama running now, then in a terminal of its own:  OLLAMA_HOST=0.0.0.0:11434 ollama serve"
    fi
    echo "    curl http://$OLLAMA/api/version    (it should print a version)"
    exit 1
}

check_model() {
    local tags answer=n
    tags="$(curl -fsS --max-time 5 "http://$OLLAMA/api/tags" 2>/dev/null || true)"
    case "$tags" in *"\"$MODEL_WANTED\""*) echo "  The model $MODEL_WANTED: yes."; return 0 ;; esac
    if [ -t 0 ] && command -v ollama >/dev/null; then
        read -r -p "  The model the bot was built with, $MODEL_WANTED, is not here. Get it now? About 19 GB. [Y/n] " answer || answer=n
        case "$answer" in
            [Nn]*) ;;
            *) if OLLAMA_HOST="$OLLAMA" ollama pull "$MODEL_WANTED"; then return 0; fi ;;
        esac
    fi
    echo "  Not getting the model now. To get it later, run: ollama pull $MODEL_WANTED"
}

# The container runs as uid 1000 and writes everything in here.
make_data() {
    mkdir -p "$DATA" 2>/dev/null || sudo mkdir -p "$DATA"
    if [ -n "$(find "$DATA" ! -uid 1000 -print -quit 2>/dev/null)" ]; then
        echo "  $DATA has to belong to uid 1000, the user in the container. Running: sudo chown -R 1000:1000 $DATA"
        sudo chown -R 1000:1000 "$DATA"
    fi
}

# A bot set up before its data folder moved to the mount keeps its files in the container: never remove that. If Docker
# can't say, it counts as keeping them.
keeps_files_inside() {
    local changed
    changed="$(docker diff "$NAME" 2>/dev/null)" || return 0
    case "$changed" in *"/PuffPAI Data"*) return 0 ;; esac
    return 1
}

# Nothing else lives in the container: everything is on the mount, so one that points elsewhere can go.
drop_stale() {
    local was
    was="$(mount_of)"
    if [ -n "$was" ] && [ "$was" != "$DATA" ]; then
        if keeps_files_inside; then
            echo "  The container $NAME uses $was and keeps that bot's files inside it, so it was left alone."
            echo "  To go on with that bot: DATA=$was ./install-writersbot.sh"
            echo "  To start new, first copy them out:  docker cp \"$NAME:/home/owner/PuffPAI Data\" .  then: docker rm -f $NAME"
            exit 1
        fi
        echo "  Removing the old container $NAME. It used $was and this run uses $DATA. That folder itself is not touched."
        docker rm -f "$NAME" >/dev/null
    fi
}

# One value from the environment this container was made with.
box_env() { docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" 2>/dev/null | sed -n "s/^$1=//p" || true; }

# The home-network address is set only when the container is made, and the bot answers at no other (HTTP 421).
REMADE=0
address_check() {
    local was n
    was="$(box_env BUILDBOT_NETWORK_ADDRESSES)"
    if [ -z "$LAN" ] || [ -z "$was" ]; then return 0; fi
    case ",$was," in *",$LAN,"*) return 0 ;; esac
    if keeps_files_inside; then
        echo "  This computer's home-network address is now $LAN, but this bot allows only its old one, $was, and it keeps the books inside its container, so the container was left alone. A browser or window at the new address gets an error."
        echo "  To go on: have your router give this computer $was again. Or use the new address, and the writer's window shortcut needs it too: docs/FOR_THE_ORIN.md section 9."
        return 0
    fi
    echo "  This computer's home-network address changed from $was to $LAN, so the container is made again with the new one. Everything of the bot's is in $DATA and stays."
    echo "  The writer's window shortcut needs the new address too: docs/FOR_THE_ORIN.md section 9."
    # The new one keeps the ports, limits and model the old one was made with.
    if [ -z "${PUBLISH+x}" ]; then
        PUBLISH="$(docker inspect -f '{{range $p, $b := .HostConfig.PortBindings}}{{range $b}}-p {{.HostPort}}:{{$p}} {{end}}{{end}}' "$NAME")"
        export PUBLISH
    fi
    n="$(docker inspect -f '{{.HostConfig.Memory}}' "$NAME")"
    if [ "${n:-0}" != 0 ]; then export MEMORY="$n"; fi
    n="$(docker inspect -f '{{.HostConfig.NanoCpus}}' "$NAME")"
    if [ "${n:-0}" != 0 ]; then CPUS="$(awk -v n="$n" 'BEGIN { print n / 1000000000 }')"; export CPUS; fi
    MODEL="${MODEL:-$(box_env PUFFPAI_ONLY_MODEL)}"; export MODEL
    docker exec "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai shutdown" >/dev/null 2>&1 || true
    docker rm -f "$NAME" >/dev/null
    REMADE=1
}

# The port a browser uses on this computer: 8423, unless PUBLISH moved it.
host_port() {
    local port
    port="$(docker port "$NAME" 8423/tcp 2>/dev/null | sed -n '1s/.*://p' || true)"
    echo "${port:-8423}"
}

check_port() {
    local who port
    if [ -n "${PUBLISH+x}" ]; then return 0; fi     # this bot has its own ports (docs/FOR_THE_ORIN.md section 6)
    if ! command -v ss >/dev/null; then return 0; fi
    port="$(ss -ltnH '( sport = :8423 or ( sport >= :19100 and sport <= :19120 ) )' 2>/dev/null | awk '{print $4}' | sed 's/.*://' | sort -n | head -n 1 || true)"
    if [ -z "$port" ]; then return 0; fi
    case "$(docker port "$NAME" 2>/dev/null || true)" in *:8423*) return 0 ;; esac
    who="$(docker ps --filter "publish=$port" --format 'the container {{.Names}}' 2>/dev/null | head -n 1 || true)"
    if [ -z "$who" ]; then
        who="$(ss -ltnpH "sport = :$port" 2>/dev/null | sed -n 's/.*users:(("\([^"]*\)",pid=\([0-9]*\).*/the program \1 (process \2)/p' | head -n 1 || true)"
    fi
    echo "Port $port on this computer is held by ${who:-another program}, so nothing was started."
    echo "If that is a BuildBot started by hand from BuildBotv2/install.sh, this stops it: $CODE/stop-buildbot.sh"
    echo "(docs/FOR_THE_ORIN.md section 8 clears it away). If it is another bot you keep, section 6 gives this one its own ports."
    exit 1
}

# The container and the relay, nothing else. Its output only matters when it fails.
box_up() {
    local out
    out="$(bash "$RUN" up)" || {
        printf '%s\n' "$out"
        case "$out" in
            *"Ollama does not answer"*)
                echo "  Ollama answers on this computer but not from inside the container, so this computer's firewall may be blocking the container."
                echo "  If ufw is active (sudo ufw status says so), run:  sudo ufw allow from 172.16.0.0/12 to any port 11434"
                echo "  If not, check iptables:  sudo iptables -L INPUT -n"
                echo "  Then run the same command again." ;;
        esac
        exit 1
    }
}

# BuildBot's own installer ends with BuildBot's own commands, which are not the ones to use here. sed always succeeds, so
# pipefail keeps the real failure.
run_install() {
    bash "$RUN" install 2>&1 | sed -e '/-buildbot\.sh/d' -e '/Skipped for now/d' -e '/When you are ready/d' -e '/PUFFPAI_BOT=/d' -e '/^ *Then /d'
}

# Installed means the command is there and no install was cut off (run.sh install leaves .installing until it succeeds).
installed() { [ -e "$DATA/code/.venv/bin/puffpai" ] && [ ! -e "$DATA/.installing" ]; }

need_terminal() {
    if [ ! -t 0 ]; then
        echo "$1 asks you questions, and there is no terminal here to ask in, so it did not start. Run it at a terminal, or over ssh with: ssh -t"
        exit 1
    fi
}

setup_done() { docker exec "$NAME" grep -qs '"complete": true' "/data/bot/puffpai/$BOT/settings/wizard.json"; }

# Is any app's install unfinished? puffpai list reads the Registry, with or without the bot running.
any_broken() {
    case "$(docker exec "$NAME" bash -c 'cd /data/bot/code && .venv/bin/puffpai list' 2>/dev/null || true)" in
        *" broken "*) return 0 ;;
    esac
    return 1
}

# Through the container, so the files belong to uid 1000 whatever this user's uid is.
place_bundle() {
    tar cf - -C "$HERE/bot" . | docker exec -i "$NAME" bash -c \
        "rm -rf /data/bot/from/writersbot && mkdir -p /data/bot/from/writersbot && tar xf - -C /data/bot/from/writersbot"
    echo "  $DATA/from/writersbot (inside the container: /data/bot/from/writersbot)"
}

# From the bundle: the bot arrives with its name, look, role, Home and apps; setup asks only the rest. This is the only
# way setup runs here, because plain "puffpai setup" asks what the bot is for, and WritersBot already knows. Setup runs
# the bot itself and refuses to start beside a running one.
setup_from_bundle() {
    need_terminal "Setup"
    if docker exec "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai shutdown" >/dev/null 2>&1; then
        echo "  The bot was running, so it was stopped for setup. Setup starts it again."
    fi
    exec docker exec -it -e PUFFPAI_SETUP_AGAIN="./install-writersbot.sh setup" "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai setup --from /data/bot/from/writersbot"
}

everyday() {
    echo "  ./install-writersbot.sh start    start the bot and open a chat here"
    echo "  ./install-writersbot.sh stop     stop the bot and the container"
    echo "  ./install-writersbot.sh pair     a sign-in code for the window or a browser, which use http://$LAN:$(host_port)"
    echo "  ./install-writersbot.sh chat     another chat, while it runs"
    echo "  ./install-writersbot.sh update   brings the bot's Core, window and own apps up to this copy, and adds any app that is new, missing or broken"
}

# Small questions and the Bridge, in the Core's own Python in the container. It reads the Registry, the Bridge, and the
# state field of each book's status.json; never a manuscript or a chat.
BOX_PY="$(cat <<'PY'
import json, sys
from puffpai_core import owner_files
from puffpai_core.cli import _call, _terminal
from puffpai_core.packages.installs import Installs
from puffpai_core.paths import state_dir
from puffpai_core.registry import Registry

state = state_dir()
ask = sys.argv[1]
if ask == "plan":                      # for each id: id, what to do, name, version here, version in this copy
    rows = {a["id"]: a for a in json.load(sys.stdin)["apps"]}
    for pid in sys.argv[2:]:
        if pid not in rows:            # an older bundle without this app: nothing to bring
            continue
        app, row = rows[pid], Registry().get(pid)
        if row is None:
            how = "missing"
        elif row.get("status") == "broken":
            how = "broken"
        elif row.get("version") == app["version"]:
            how = "current"
        elif (Installs(state).get(pid) or {}).get("source") == "/data/bot/from/writersbot/" + app["source"]:
            how = "update"
        else:
            how = "replace"            # it came from somewhere else, such as the store
        print("	".join((pid, how, app["name"], str((row or {}).get("version") or "none"), app["version"])))
elif ask == "reading":                 # yes, no, or unsure. A status.json outlives a stopped Manuscript, so it also asks the tool.
    said = "no"
    books = []
    for path in (owner_files.my_files(state) / "Manuscripts").glob("*/status.json"):
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("state") == "reading":
                books.append(path.parent.name)
        except (OSError, ValueError, AttributeError):
            pass
    if books and (Registry().get("manuscript") or {}).get("status") == "running":
        info = _terminal()
        for title in books:
            try:
                status, body = _call(info, "POST", "/v1/chat/tool", {"tool": "manuscript.manuscript_status", "args": {"title": title}})
            except (OSError, TypeError):
                status, body = 0, ""
            text = body.get("result") if isinstance(body, dict) else None
            if status != 200 or not isinstance(text, str):
                said = "unsure"
            elif "The reading stopped when Manuscript stopped" not in text:
                said = "yes"
                break
    print(said)
elif ask == "bridge":                  # no word: what the Bridge is set to now. open or close: change it, the same.
    info = _terminal()
    if len(sys.argv) > 2:
        status, body = _call(info, "POST", "/v1/bridge/" + sys.argv[2], {})
    else:
        status, body = _call(info, "GET", "/v1/bridge")
    if status != 200 or not isinstance(body, dict):
        print(body.get("error") if isinstance(body, dict) else body)
        sys.exit(1)
    print(body.get("setting"))
elif ask == "topics":                  # the bundle's reading list, for Recommended for this bot: only this one key of this one file
    from puffpai_core import jsonfile
    topics = (json.load(sys.stdin).get("knowledge") or {}).get("topics") or []
    path = state / "settings" / "moved_list.json"
    got = jsonfile.read(path, {})
    got = got if isinstance(got, dict) else {}
    if topics and got.get("topics") != topics:
        got["topics"] = topics
        jsonfile.write(path, got)
        print("added")
    else:
        print("same")
PY
)"
box_py() { docker exec -i "$NAME" /data/bot/code/.venv/bin/python -c "$BOX_PY" "$@"; }
box_cli() { docker exec -it "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai $1"; }

# Her data never goes through here: only these folders of the code, never .venv, the launchers or a cache. The Core reads
# contracts/ and templates/ as it runs.
PARTS_SUMS='find core packages contracts templates -type f ! -path "*/__pycache__/*" ! -name "*.pyc" ! -path "*.egg-info/*" ! -path "*/.venv/*" ! -path "*/node_modules/*" -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum'
CORE_CHANGED=0

UPDATE_ORDER="novel-mode manuscript editors-desk maps document-convert"
BUNDLE_RECORD="$HERE/bot/puffpai-bot.json"

core_answers() { docker exec "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai bridge status" >/dev/null 2>&1; }

# Started here with no hidden chat, the same way as docs/FOR_THE_ORIN.md section 4.
CORE_STARTED=0
core_up() {
    local i
    if core_answers; then return 0; fi
    echo "  The bot is not running, so it is started now, in the background."
    docker exec -d "$NAME" bash -c "cd /data/bot/code && exec .venv/bin/puffpai serve >>/data/bot/puffpai/$BOT/logs/serve.out 2>&1"
    for i in $(seq 1 40); do
        sleep 3
        if core_answers; then CORE_STARTED=1; return 0; fi
    done
    echo "  The bot did not start within two minutes, so nothing was updated. To see why: docker exec $NAME tail /data/bot/puffpai/$BOT/logs/serve.out"
    exit 1
}

# An update restarts the app, which would end a reading. A reading can't outlive the Core this run just started.
stop_if_reading() {
    local got
    if [ "$CORE_STARTED" = 1 ]; then return 0; fi
    got="$(box_py reading </dev/null)" || got=unsure
    case "$got" in
        no) return 0 ;;
        yes) echo "  Manuscript is reading a book right now, and an update restarts it, which would stop the reading. $1 Run this again when the reading is done." ;;
        *) echo "  A book's status says a reading is running, and Manuscript could not confirm it, so it counts as running: an update would stop it. $1 Run this again when the reading is done." ;;
    esac
    exit 1
}

core_stop() {
    local i
    docker exec "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai shutdown" >/dev/null 2>&1 || true
    for i in $(seq 1 30); do
        if ! core_answers; then return 0; fi
        sleep 2
    done
    return 1
}

# Detached, in the container that is already there: the container is never restarted, since an older bot keeps its books
# inside it. Its process number goes on the mount, so a Core that never answers can be stopped by that number alone.
core_start_and_wait() {
    local i
    docker exec -d "$NAME" bash -c "cd /data/bot/code && echo \$\$ >/data/bot/tmp/core-update.pid && exec .venv/bin/puffpai serve >>/data/bot/puffpai/$BOT/logs/serve.out 2>&1"
    for i in $(seq 1 40); do
        sleep 3
        if core_answers; then return 0; fi
    done
    return 1
}

core_kill_started() {
    docker exec "$NAME" bash -c 'p="$(cat /data/bot/tmp/core-update.pid 2>/dev/null)"; [ -n "$p" ] && kill "$p" 2>/dev/null; sleep 5; [ -n "$p" ] && kill -9 "$p" 2>/dev/null; true' >/dev/null 2>&1 || true
}

# The Core and any built-in package that differ from this copy. At its next start the Core itself brings its installed copy
# of the window up to packages/desktop (PackageManager.check_builtins), which is why the Core must restart. pyproject.toml has
# not changed since the first public version, so nothing is installed with pip.
update_core() {
    local here box parts gone fresh
    here="$(cd "$CODE" && bash -c "$PARTS_SUMS" | LC_ALL=C sort)"
    box="$(docker exec "$NAME" bash -c "cd /data/bot/code && $PARTS_SUMS" | LC_ALL=C sort)" || { echo "  The bot's Core could not be read, so nothing was updated."; exit 1; }
    parts="$(LC_ALL=C comm -3 <(printf '%s\n' "$here") <(printf '%s\n' "$box") | sed -E -e 's/^[[:space:]]*//' -e 's/^[0-9a-f]+ [ *]//' | awk -F/ 'NF { print ($1 == "packages" ? $1 "/" $2 : $1) }' | LC_ALL=C sort -u)"
    parts="$(for p in $parts; do if [ -e "$CODE/$p" ]; then echo "$p"; fi; done)"
    if [ -z "$parts" ]; then
        echo "  The Core and the window are the same as this copy."
        return 0
    fi
    if core_answers; then stop_if_reading "Nothing was changed."; fi
    echo "  The bot's Core and window are older than this copy, so they are updated now, and the Core restarts. Its vault, settings, chats and books are not touched."
    if core_answers; then
        if ! core_stop; then echo "  The bot did not stop, so nothing was changed. Try again, or: ./install-writersbot.sh stop"; exit 1; fi
    fi
    # shellcheck disable=SC2086
    docker exec "$NAME" bash -c "cd /data/bot/code && for p in $(echo $parts); do if [ -e \"\$p\" ]; then echo \"\$p\"; fi; done | tar cf /data/bot/before-update.tar --exclude=__pycache__ --exclude='*.pyc' --exclude='*.egg-info' --exclude=.venv -T -" \
        || { echo "  A copy of the old Core could not be kept on the data folder, so nothing was changed. Is its disk full?"; exit 1; }
    gone="$(LC_ALL=C comm -13 <(printf '%s\n' "$here" | sed -E 's/^[0-9a-f]+ [ *]//' | LC_ALL=C sort) <(printf '%s\n' "$box" | sed -E 's/^[0-9a-f]+ [ *]//' | LC_ALL=C sort))"
    fresh="$(LC_ALL=C comm -23 <(printf '%s\n' "$here" | sed -E 's/^[0-9a-f]+ [ *]//' | LC_ALL=C sort) <(printf '%s\n' "$box" | sed -E 's/^[0-9a-f]+ [ *]//' | LC_ALL=C sort))"
    # shellcheck disable=SC2086
    tar cf - -C "$CODE" --exclude=.venv --exclude=bots --exclude='*-buildbot.sh' --exclude=__pycache__ --exclude='*.pyc' --exclude='*.egg-info' $parts \
        | docker exec -i "$NAME" tar xf - -C /data/bot/code
    remove_listed "$gone"
    CORE_CHANGED=1
    CORE_STARTED=1
    if core_start_and_wait; then
        echo "  The bot's Core is back, on the new code."
        case " $(echo $parts) " in *" packages/desktop "*) ;; *) return 0 ;; esac
        if [ "$(docker exec "$NAME" bash -c "cd /data/bot/code && sha256sum packages/desktop/web/pages/knowledge.js | cut -d' ' -f1" 2>/dev/null)" != "$(docker exec "$NAME" bash -c "cd /data/bot/puffpai/$BOT/packages/desktop/web/pages && sha256sum knowledge.js | cut -d' ' -f1" 2>/dev/null || true)" ]; then
            echo "  The window's new pages did not come through by themselves. Look for their Yes card on Your turn, or run: docker exec -it $NAME bash -c \"cd /data/bot/code && .venv/bin/puffpai update desktop\""
        fi
        return 0
    fi
    echo "  The bot did not come back on the new code within two minutes, so the old code is put back."
    core_kill_started
    remove_listed "$fresh"
    docker exec "$NAME" tar xf /data/bot/before-update.tar -C /data/bot/code
    if core_start_and_wait; then
        echo "  The bot is running again on its old code, and nothing else was changed. To see why the new code did not start: docker exec $NAME tail /data/bot/puffpai/$BOT/logs/serve.out"
    else
        echo "  The bot did not start on its old code either. A copy of that code is at /data/bot/before-update.tar in the container. To see why: docker exec $NAME tail /data/bot/puffpai/$BOT/logs/serve.out"
    fi
    exit 1
}

remove_listed() {
    if [ -n "$1" ]; then
        printf '%s\n' "$1" | docker exec -i "$NAME" bash -c 'cd /data/bot/code && while IFS= read -r f; do rm -f -- "$f"; done'
    fi
}

# The books and articles the bot came with. A bot set up from an older bundle never had this list.
seed_topics() {
    local got
    got="$(box_py topics <"$BUNDLE_RECORD" 2>/dev/null)" || got=""
    if [ "$got" = added ]; then
        echo "  The books and articles this bot came with are on Knowledge now, under Recommended for this bot. Nothing downloads until you say Yes there."
    fi
}

BRIDGE_OPENED=0
close_bridge_again() {
    if [ "$BRIDGE_OPENED" = 1 ]; then
        BRIDGE_OPENED=0
        if box_py bridge close </dev/null >/dev/null; then
            echo "  The Bridge is closed again, the way you had it."
        else
            echo "  The Bridge is still open. To close it, use the Bridge page."
        fi
    fi
}

bridge_for_update() {
    local now answer
    now="$(box_py bridge </dev/null)" || { echo "  The Bridge did not answer, so nothing was updated. $now"; exit 1; }
    if [ "$now" = open ]; then
        echo "  The Bridge is open, so the apps can download what they need."
        return 0
    fi
    read -r -p "  This bot's main apps can't install while the Bridge is closed, because they download what they need. Open it for this? [Y/n] " answer || answer=n
    case "$answer" in
        [Nn]*) echo "  The Bridge stays closed, so nothing was updated."; exit 0 ;;
    esac
    now="$(box_py bridge open </dev/null)" || { echo "  The Bridge did not open, so nothing was updated. $now"; exit 1; }
    if [ "$now" != open ]; then echo "  The Bridge did not open, so nothing was updated."; exit 1; fi
    BRIDGE_OPENED=1
    trap close_bridge_again EXIT
}

update_bot() {
    local plan id how name have want answer needs_bridge=0 pending=0 left=0
    say "Updating this bot from this copy"
    if keeps_files_inside; then
        echo "  This bot's books are inside its container, and this update leaves them there. To back them up: docs/FOR_THE_ORIN.md section 8."
    fi
    update_core
    seed_topics
    plan="$(box_py plan $UPDATE_ORDER <"$BUNDLE_RECORD")" || { echo "  The bot's list of apps could not be read, so nothing was updated."; exit 1; }
    while IFS=$'	' read -r id how name have want; do
        # Novel mode is only text, and Maps and Editor's Desk need nothing downloaded (run.default.setup in their puffpai.json).
        case "$how" in
            update|replace|broken|missing)
                pending=1
                case "$id" in manuscript|document-convert) needs_bridge=1 ;; esac ;;
        esac
    done <<<"$plan"
    if [ "$pending" = 1 ]; then
        need_terminal "Updating these apps"
        core_up
        stop_if_reading "Nothing was changed."
        if [ "$needs_bridge" = 1 ]; then bridge_for_update; fi
    fi
    echo "  Copying the bundle:"
    place_bundle
    # Fd 3, because each step below reads its answers from the terminal.
    while IFS=$'	' read -r id how name have want <&3; do
        case "$how" in
            current) echo; echo "  $name is at $want already, so it is skipped." ;;
            missing)
                echo; echo "  Adding $name $want to this bot. Its Yes card comes next; say No to leave it out."
                box_cli "add /data/bot/from/writersbot/apps/$id" || true ;;
            broken)
                echo; echo "  $name did not finish installing, so it is taken out and added again from this copy. Its data stays."
                if box_cli "remove $id --keep-data"; then box_cli "add /data/bot/from/writersbot/apps/$id" || true; fi ;;
            update)
                echo; echo "  $name: $have to $want. An update restarts it."
                if [ "$id" = manuscript ]; then stop_if_reading "The apps before it are done; the rest were not changed."; fi
                box_cli "update $id" || true ;;
            replace)
                echo; echo "  $name: $have to $want. The one here was added from another folder or from the store, so it is taken out and added again from this copy. Its data stays."
                read -r -p "  Its Yes card comes next. If you say No there, it stays out until you run this again. Go on? [Y/n] " answer || answer=n
                case "$answer" in
                    [Nn]*) echo "  $name was left as it is." ;;
                    *) if box_cli "remove $id --keep-data"; then box_cli "add /data/bot/from/writersbot/apps/$id" || true; fi ;;
                esac ;;
        esac
    done 3<<<"$plan"
    close_bridge_again
    echo
    plan="$(box_py plan $UPDATE_ORDER <"$BUNDLE_RECORD" || true)"
    while IFS=$'	' read -r id how name have want; do
        case "$how" in
            update|replace) echo "  $name is still at $have, not $want. To try again: ./install-writersbot.sh update"; left=1 ;;
            broken) echo "  $name did not finish installing. To try again: ./install-writersbot.sh update"; left=1 ;;
            missing) echo "  $name was not added. To add it: ./install-writersbot.sh update"; left=1 ;;
        esac
    done <<<"$plan"
    if [ "$left" = 1 ]; then
        echo "  To see why an app failed: docker exec -it $NAME bash -c \"cd /data/bot/code && .venv/bin/puffpai logs <its id>\""
        say "Not every app finished. Reload the window once (press F5) for the ones that did."
    elif [ "$pending" = 1 ] || [ "$CORE_CHANGED" = 1 ]; then
        say "Done. Reload the window once (press F5), so its pages open again."
        if [ "$CORE_CHANGED" = 1 ]; then echo "  A restart locks the vault. If the window shows a PIN box at the top, type your PIN there."; fi
        if [ "$CORE_STARTED" = 1 ]; then echo "  The bot is running in the background. ./install-writersbot.sh stop stops it."; fi
    else
        say "Everything is up to date. Nothing else was changed."
    fi
    if [ "$left" = 1 ]; then exit 1; fi
}

case "$verb" in
    stop)
        if [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null || true)" != true ]; then
            echo "WritersBot is not running, so there is nothing to stop."
            exit 0
        fi
        exec bash "$RUN" stop ;;
    update)
        if ! installed; then echo "WritersBot is not installed in $DATA yet. Run ./install-writersbot.sh first."; exit 1; fi
        check_port
        box_up
        if ! setup_done; then echo "Setup has not finished, so there is nothing to update yet. Setup brings the newest apps: ./install-writersbot.sh setup"; exit 1; fi
        update_bot
        exit 0 ;;
    setup|start|pair|chat)
        if [ "$verb" = setup ]; then need_terminal "Setup"; fi
        if [ "$verb" = start ]; then echo "  Later, to stop it: ./install-writersbot.sh stop (the stop-buildbot.sh it may name is not on this computer)"; fi
        if ! installed; then echo "WritersBot is not installed in $DATA yet. Run ./install-writersbot.sh first."; exit 1; fi
        drop_stale
        case "$verb" in start|pair) address_check ;; esac
        check_port
        box_up
        if [ "$verb" != setup ]; then
            if setup_done; then
                if [ "$verb" = pair ]; then
                    bash "$RUN" pair
                    if [ "$(host_port)" != 8423 ]; then echo "On this computer the bot's home-network port is $(host_port), so use http://$LAN:$(host_port) instead of 8423."; fi
                    if [ "$REMADE" = 1 ]; then echo "The bot stopped when its container was made again. For that code to work, start it: ./install-writersbot.sh start"; fi
                    exit 0
                fi
                exec bash "$RUN" "$verb"
            fi
            # A window opened on a bot that is not set up would start the plain questions, so setup comes first.
            say "Setup has not finished, so it comes first."
        fi
        # On a bot set up by the first version, setup would put back the name, the look and the role. update is the repair there.
        if setup_done && ! docker exec "$NAME" grep -qs take_moved /data/bot/code/core/puffpai_core/wizard/terminal.py; then
            echo "Setup already finished on this bot, and running it again here would put back the name, the look and the role it came with."
            echo "So it did not run. To bring the apps up to date or add one back: ./install-writersbot.sh update"
            exit 0
        fi
        place_bundle                # always this clone's copy, so a repair after git pull uses the new apps
        setup_from_bundle ;;
esac
NO_SETUP=0; PICTURES=0
for a in "$@"; do case "$a" in --no-setup) NO_SETUP=1 ;; --pictures) PICTURES=1 ;; esac; done

say "Checking this computer"
command -v docker >/dev/null || { echo "Docker is missing. On a Jetson with JetPack it is there already; otherwise install Docker first."; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running, or this user may not use it. Try: sudo usermod -aG docker \$USER, then log out and in."; exit 1; }
check_ollama
echo "  Docker: yes. Ollama: yes, at $OLLAMA. Home-network address: $LAN. Data folder: $DATA"
check_model
make_data
drop_stale
address_check
check_port

say "Building the image ($IMAGE)"
echo "  A few minutes of technical lines scrolling by is normal here."
bash "$RUN" build

if [ -e "$DATA/.installing" ]; then
    # Started with no terminal, it goes on inside the container after Ctrl+C. Setup has never run, so nothing of hers is in these.
    say "An earlier install was cut off, so it is stopped and cleared, and the install runs again"
    docker restart "$NAME" >/dev/null 2>&1 || true
    box_up
    docker exec "$NAME" rm -rf /data/bot/code /data/bot/puffpai
fi

if installed; then
    say "BuildBot is already installed in $DATA, so that step is skipped"
    box_up
else
    say "Installing BuildBot into the container (WritersBot's own version)"
    box_up
    run_install
fi

SET_UP=0
if setup_done; then SET_UP=1; fi
if [ "$SET_UP" = 0 ]; then
    say "Placing the bot bundle where setup finds it"
    place_bundle
fi

if [ "$PICTURES" = 1 ]; then
    say "The picture program for Maps, on this Orin"
    bash "$HERE/install-pictures.sh" || echo "  The picture program did not finish. WritersBot works without it; run ./install-pictures.sh again later."
fi

if [ "$SET_UP" = 1 ]; then
    say "Setup is already done, so it is not run again. Everyday commands:"
    everyday
    if any_broken; then
        echo
        echo "  An app did not finish installing. To add it again: ./install-writersbot.sh update"
    fi
    exit 0
fi

if [ "$NO_SETUP" = 1 ]; then
    say "Installed. When ready:  ./install-writersbot.sh setup"
    exit 0
fi

say "Setup. It asks about the model, the vault and its PIN, the Bridge, and Toolsmith's model. Write the recovery code down."
echo "Then each app shows its Yes card here, in this terminal. Type y for each. START_HERE.md lists every question."
echo "When it says BuildBot is running, the window on the writer's Windows computer uses:  http://$LAN:$(host_port)"
echo "(docs/FOR_YOUR_WINDOWS_COMPUTER.md, Part 1)."
echo
setup_from_bundle
