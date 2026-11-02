#!/usr/bin/env bash
# A BuildBot inside one container (D132): runs as uid 1000, with no Docker of its own, using an Ollama that already runs
# on the host. No sudo in it, no docker.sock, no --privileged, no host networking. Removing the container and the data
# folder removes everything it kept, keys included.
#
#   CODE=/path/to/export DATA=/data/bot OLLAMA=172.17.0.1:11434 LAN=192.168.1.50 MODEL=qwen2.5:7b-instruct scripts/container/run.sh build
#   ... run.sh install     once: the relay, then install.sh, all on the data folder
#   ... run.sh up          the container and the relay, nothing else
#   ... run.sh setup       setup, in this terminal
#   ... run.sh start       start the bot in the background and open the chat here
#   ... run.sh chat        another chat, while it runs
#   ... run.sh stop        stop the bot and the container
#   ... run.sh pair        a sign-in code for a browser on your home network
#
# MODEL is the one model this bot may use on the host's Ollama. It is the default, nothing else loads, and nothing is
# downloaded. Leave it unset only if this bot may use any model there and start downloads. LAN is the address a browser on your home network uses for this computer. Ports published on the host are the bot's
# own numbers: the home-network listener (8423) and its app pages (19100-19120), and they have to stay the same.
set -euo pipefail

NAME="${NAME:-buildbot-box}"
BOT="${BOT:-buildbot}"
IMAGE="${IMAGE:-puffpai-box}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE="${CODE:-$(cd "$HERE/../.." && pwd)}"
: "${DATA:?Set DATA to the folder that holds everything this bot keeps. uid 1000 has to be able to write to it.}"
OLLAMA="${OLLAMA:-172.17.0.1:11434}"
MEMORY="${MEMORY:-8g}"
CPUS="${CPUS:-4}"
PLATFORM_ARG=()
[ -n "${PLATFORM:-}" ] && PLATFORM_ARG=(--platform "$PLATFORM")

in_box() { docker exec -it "$NAME" bash -c "$1"; }
batch() { docker exec "$NAME" bash -c "$1"; }
GO="cd /data/bot/code"

up() {
    if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
        if ! docker start "$NAME" >/dev/null 2>&1; then
            # A bot set up before its data folder moved to the mount keeps its files in the container: never remove that.
            case "$(docker diff "$NAME" 2>/dev/null || true)" in
                *"/PuffPAI Data"*)
                    echo "$NAME did not start, and it keeps this bot's files inside it, so it was left alone. To see why: docker start $NAME"
                    exit 1 ;;
            esac
            # A failed docker run leaves a dead container under this name, which would block the next try. Nothing lives
            # in it: everything is on the mount. Clear it before and after.
            docker rm -f "$NAME" >/dev/null 2>&1 || true
            # Set on the container, so every process in it has them, whatever starts it. --memory-swap equal to --memory: no swap.
            docker run -d --init --name "$NAME" -u 1000 \
                --memory "$MEMORY" --memory-swap "$MEMORY" --cpus "$CPUS" \
                "${PLATFORM_ARG[@]}" ${EXTRA_RUN:-} -v "$DATA:/data/bot" ${PUBLISH--p 8423:8423 -p 19100-19120:19100-19120} \
                -e PUFFPAI_BOT="$BOT" -e PUFFPAI_HOME=/data/bot/puffpai -e PUFFPAI_ONLY_MODEL="${MODEL:-}" \
                -e BUILDBOT_NETWORK_ADDRESSES="${LAN:-}" -e TMPDIR=/data/bot/tmp -e XDG_CACHE_HOME=/data/bot/cache \
                -e PUFFPAI_BROWSER_NO_SANDBOX=1 -e PUFFPAI_HOST_EXEC="docker exec -it $NAME" -e PIP_CACHE_DIR=/data/bot/cache/pip -e npm_config_cache=/data/bot/cache/npm "$IMAGE" sleep infinity >/dev/null \
                || { docker rm -f "$NAME" >/dev/null 2>&1 || true; exit 1; }
        fi
    fi
    # The Core asks Ollama on 127.0.0.1 only: a small relay inside, up before anything else runs.
    batch "mkdir -p /data/bot/tmp /data/bot/cache; pgrep -x socat >/dev/null || (nohup socat TCP-LISTEN:11434,bind=127.0.0.1,fork,reuseaddr TCP:$OLLAMA >/dev/null 2>&1 &) ; for i in 1 2 3 4 5; do curl -fsS --max-time 3 http://127.0.0.1:11434/api/version >/dev/null && exit 0; sleep 1; done; echo 'Ollama does not answer at $OLLAMA, so nothing was started.'; exit 1"
}

case "${1:-}" in
    build)
        docker build "${PLATFORM_ARG[@]}" -t "$IMAGE" "$HERE" ;;
    install)
        up
        # On the mount until install.sh succeeds. With no terminal here, Ctrl+C leaves it running in the container and
        # docker exec still returns 0, so the marker comes off in the same command, inside the container.
        batch "mkdir -p /data/bot/code; touch /data/bot/.installing"
        (cd "$CODE" && tar cf - --exclude=.git --exclude=.venv --exclude=bots --exclude='*-buildbot.sh' .) | docker exec -i "$NAME" tar xf - -C /data/bot/code
        batch "cd /data/bot/code && bash install.sh --data-dir /data/bot --bot $BOT --no-tools --no-setup </dev/null && rm -f /data/bot/.installing" ;;
    up)
        up ;;
    setup)
        up
        # This bot comes as a bundle. It never gets the plain setup, which asks what it is for.
        if batch "[ -d /data/bot/from/writersbot ]"; then
            in_box "$GO; .venv/bin/puffpai setup --from /data/bot/from/writersbot"
        else
            echo "The bot's bundle is not in place. Run setup with: ./install-writersbot.sh setup"
            exit 1
        fi ;;
    start)
        up; in_box "$GO; .venv/bin/puffpai start" ;;
    chat)
        up; in_box "$GO; .venv/bin/puffpai" ;;
    pair)
        up; batch "$GO; .venv/bin/puffpai pair" ;;
    stop)
        batch "$GO; .venv/bin/puffpai shutdown" || true
        docker stop "$NAME" >/dev/null ;;
    *)
        sed -n 2,18p "${BASH_SOURCE[0]}" ;;
esac
