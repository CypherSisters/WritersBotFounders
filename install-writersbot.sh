#!/usr/bin/env bash
# WritersBotFounders on a Jetson Orin: the same recipe as the box it was built on. One container, uid 1000, on Docker's
# bridge network, 8 GB of memory and 4 CPUs, using the Ollama that already runs on the Orin. No sudo in the container,
# no docker.sock, no --privileged, no host networking. Removing the container and the data folder removes everything.
#
#   ./install-writersbot.sh                      build the image, install, then setup (asks about the model, the vault, the Bridge, Toolsmith)
#   ./install-writersbot.sh --no-setup           build and install only; run setup later with: ./install-writersbot.sh setup
#   ./install-writersbot.sh setup|start|stop|pair|chat     the bot's everyday commands, same as the box it came from
#   ./install-writersbot.sh --pictures           also set up the picture program on this Orin (install-pictures.sh)
#
# Settings you may change, as environment variables: DATA (where the bot keeps everything, default /data/puffpai/writersbot),
# LAN (this Orin's home-network address; found by itself when empty), MEMORY (8g), CPUS (4), OLLAMA (172.17.0.1:11434).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NAME="${NAME:-writersbot-box}"
export BOT="${BOT:-writersbot}"
export IMAGE="${IMAGE:-puffpai-box}"
export DATA="${DATA:-/data/puffpai/writersbot}"
export OLLAMA="${OLLAMA:-172.17.0.1:11434}"
export MEMORY="${MEMORY:-8g}"
export CPUS="${CPUS:-4}"
export CODE="$HERE/BuildBotv2"
RUN="$CODE/scripts/container/run.sh"
if [ -z "${LAN:-}" ]; then LAN="$(hostname -I 2>/dev/null | awk '{print $1}')"; fi
export LAN

say() { printf '\n== %s\n' "$*"; }
in_box() { docker exec -it "$NAME" bash -c "$1"; }

verb="${1:-install}"
case "$verb" in
    setup|start|stop|pair|chat)
        if [ "$verb" = setup ] && [ -d "$DATA/from/writersbot" ]; then
            # setup from the bundle: the bot arrives with its name, look, role, Home and apps; setup asks only the rest
            bash "$RUN" start >/dev/null 2>&1 || true
            exec docker exec -it "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai setup --from /data/bot/from/writersbot"
        fi
        exec bash "$RUN" "$verb" ;;
    install|--no-setup|--pictures) ;;
    -h|--help) sed -n 2,13p "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown: $verb"; sed -n 2,13p "${BASH_SOURCE[0]}"; exit 1 ;;
esac
NO_SETUP=0; PICTURES=0
for a in "$@"; do case "$a" in --no-setup) NO_SETUP=1 ;; --pictures) PICTURES=1 ;; esac; done

say "Checking this computer"
command -v docker >/dev/null || { echo "Docker is missing. On a Jetson with JetPack it is there already; otherwise install Docker first."; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running, or this user may not use it. Try: sudo usermod -aG docker \$USER, then log out and in."; exit 1; }
if ! curl -fsS --max-time 3 "http://${OLLAMA%%:*}:${OLLAMA##*:}/api/version" >/dev/null 2>&1 && ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "  Ollama does not answer from here. The container asks it at $OLLAMA. If setup finds no brain: install Ollama (ollama.com),"
    echo "  make it listen for the container (OLLAMA_HOST=0.0.0.0 in its service), and pull the model: ollama pull qwen3:30b-a3b"
fi
echo "  Docker: yes. Ollama: yes. Home-network address: $LAN. Data folder: $DATA"
mkdir -p "$DATA" || { echo "Cannot make $DATA. Make it and give uid 1000 write access: sudo mkdir -p $DATA && sudo chown 1000:1000 $DATA"; exit 1; }
[ -w "$DATA" ] || { echo "$DATA is not writable. sudo chown 1000:1000 $DATA, or run with DATA=<a folder you own>."; exit 1; }

say "Building the image ($IMAGE)"
bash "$RUN" build

say "Installing BuildBot into the container (WritersBot's own version)"
bash "$RUN" install

say "Placing the bot bundle where setup finds it"
mkdir -p "$DATA/from"
rm -rf "$DATA/from/writersbot"
cp -r "$HERE/bot" "$DATA/from/writersbot"
echo "  $DATA/from/writersbot (inside the container: /data/bot/from/writersbot)"

if [ "$PICTURES" = 1 ]; then
    say "The picture program for Maps, on this Orin"
    bash "$HERE/install-pictures.sh" || echo "  The picture program did not finish. WritersBot works without it; run ./install-pictures.sh again later."
fi

if [ "$NO_SETUP" = 1 ]; then
    say "Installed. When ready:  ./install-writersbot.sh setup"
    exit 0
fi

say "Setup. It asks about the model, the vault and its PIN, the Bridge, and Toolsmith's model. Write the recovery code down."
echo "When it says BuildBot is running, the window on the writer's Windows computer uses:  http://$LAN:8423"
echo "(docs/FOR_YOUR_WINDOWS_COMPUTER.md, Part 1). Each app asks its own Yes card the first time the window opens."
echo
exec docker exec -it "$NAME" bash -c "cd /data/bot/code && .venv/bin/puffpai setup --from /data/bot/from/writersbot"
