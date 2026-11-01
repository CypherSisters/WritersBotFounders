#!/usr/bin/env bash
# PuffPAI BuildBot installer (Linux and Mac, D132). The same steps as install.ps1.
# Harvested from PuffPAIBuildBot (bb-next):install.sh (rewritten-from): the plain output, the dry run, one yes for the
# downloads, Ollama's own script on Linux.
#
# From a fresh clone:
#     ./install.sh
#
# It:
#   1. finds Python 3.11 or newer, Node.js and Ollama. For what is missing it asks once, then downloads pinned copies
#      into a tools folder of their own (scripts/install_tools.sh). No sudo. On Linux, Ollama comes from its own official
#      script, after its own yes, since that script asks for your password,
#   2. makes .venv next to this file and installs BuildBot into it,
#   3. makes your bot's own folder (a folder that is already there is never used or deleted: the new bot gets
#      <bot name>-2 and it says so),
#   4. writes start-buildbot.sh, stop-buildbot.sh, chat-buildbot.sh and signin-buildbot.sh next to this file
#      (on a Mac also "Start BuildBot.command" and "Stop BuildBot.command", to double-click),
#   5. runs "puffpai setup".
#
# Everything it prints is also kept in install-log.txt next to this file, up to setup: setup shows PINs and a recovery
# code, which never go in a log.
#
# Test hook (not for owners): PUFFPAI_INSTALL_PRETEND_MISSING=python,node,ollama makes those look missing, so the
# missing path can be run with --dry-run on a computer that has them.
#
# ASCII only in what it prints. bash 3.2 is enough: that is what a Mac has.

set -euo pipefail

PY_MIN_MAJOR=3
PY_MIN_MINOR=11          # pyproject.toml: requires-python = ">=3.11"
BOT="buildbot"
PYTHON=""
NO_SETUP=0
FROM_FOLDER=""
DRY_RUN=0
YES_TOOLS=0
YES_OLLAMA=0
NO_TOOLS=0
DATA_DIR=""

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$REPO_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"
PUFFPAI="$VENV_DIR/bin/puffpai"
LOG_FILE="$REPO_DIR/install-log.txt"

say() { printf '%s\n' "$*"; }
step() { say ""; say "Step $1 of 5: $2"; }
fail() { say ""; say "Setup stopped: $*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<EOF
BuildBot setup for Linux and Mac.

    ./install.sh [--bot NAME] [--data-dir FOLDER] [--python FILE] [--from FOLDER] [--no-setup] [--dry-run]

  --bot NAME     your bot's name (default buildbot). A folder already there is
                 kept as it is; the new bot gets NAME-2 (or -3, ...).
  --data-dir FOLDER
                 keep your bot, the downloaded programs and all temporary files
                 under this folder, for when your home disk is small
  --python FILE  use this Python (3.11 or newer)
  --no-setup     do everything but "puffpai setup" (run it yourself later)
  --from FOLDER  a bot moved from another computer (puffpai export). Setup asks only
                 what can't travel.
  --dry-run      show every step and every file it would write, and change nothing
  --yes          download what is missing without asking
  --yes-ollama   on Linux, install Ollama with its official script without asking
  --no-tools     never download anything; say what is missing instead
  --help         this text
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --bot)        [ "$#" -ge 2 ] || fail "--bot needs a name."; BOT="$2"; shift ;;
        --python)     [ "$#" -ge 2 ] || fail "--python needs a file."; PYTHON="$2"; shift ;;
        --data-dir)   [ "$#" -ge 2 ] || fail "--data-dir needs a folder."; DATA_DIR="$2"; shift ;;
        --from)       [ "$#" -ge 2 ] || fail "--from needs a folder."; FROM_FOLDER="$2"; shift ;;
        --no-setup)   NO_SETUP=1 ;;
        --dry-run)    DRY_RUN=1 ;;
        --yes)        YES_TOOLS=1 ;;
        --yes-ollama) YES_OLLAMA=1 ;;
        --no-tools)   NO_TOOLS=1 ;;
        --help|-h)    usage; exit 0 ;;
        *)            usage; fail "'$1' is not something setup knows." ;;
    esac
    shift
done

BOT_SHAPE='^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$'
if ! [[ "$BOT" =~ $BOT_SHAPE ]] || [[ "$BOT" == *..* ]]; then
    fail "'$BOT' can't be a bot name. Use letters, numbers, spaces, dots, dashes or underscores."
fi

if [ -n "$DATA_DIR" ]; then
    case "$DATA_DIR" in /*) ;; *) fail "Give --data-dir as a full path, starting with /." ;; esac
    export PUFFPAI_HOME="${DATA_DIR%/}/puffpai"
    export PUFFPAI_TOOLS_DIR="${DATA_DIR%/}/puffpai-tools"
fi

. "$REPO_DIR/scripts/install_tools.sh"

# Downloads and builds never fill /tmp or ~/.cache, which can be a small disk: their temporary files stay next to the
# install while it runs, and go when it ends.
WORK_DIR="${DATA_DIR:-$REPO_DIR}"
WORK_DIR="${WORK_DIR%/}/.install-tmp"
if [ "$DRY_RUN" = "0" ]; then
    mkdir -p "$WORK_DIR/tmp" "$WORK_DIR/pip" || fail "Could not write to $WORK_DIR."
    export TMPDIR="$WORK_DIR/tmp" PIP_CACHE_DIR="$WORK_DIR/pip"
    trap 'rm -rf "$WORK_DIR"' EXIT
fi

pretends() {
    case ",${PUFFPAI_INSTALL_PRETEND_MISSING:-}," in *",$1,"*) return 0 ;; esac
    return 1
}

CAN_ASK=0
if [ "$DRY_RUN" = "0" ] && [ -t 0 ]; then CAN_ASK=1; fi
ask_yes_no() {
    _answer=""
    printf '%s (y/n) ' "$1"
    read -r _answer || return 1
    case "$_answer" in [Yy]*) return 0 ;; esac
    return 1
}

# Everything from here on also goes to the log, until setup starts.
if [ "$DRY_RUN" = "0" ]; then
    exec 3>&1 4>&2
    exec > >(tee "$LOG_FILE") 2>&1
fi

say ""
say "BuildBot setup"
say "This gets BuildBot ready on this computer. Nothing leaves your computer without your consent."
[ "$DRY_RUN" = "1" ] && say "(Practice run: it shows each step and changes nothing.)"

IS_MAC=0
[ "$(uname -s 2>/dev/null)" = "Darwin" ] && IS_MAC=1
PLATFORM="$(tools_platform)"
TOOLS_DIR="$(tools_default_dir)"

# --- 1. Python, Node.js, Ollama ------------------------------------------------
step 1 "Python, Node.js and Ollama"

# tools_python_ok checks venv and ssl. Debian and Ubuntu split ensurepip off into python3-venv, and without it a venv
# can't be made, so it counts as missing here.
python_usable() { tools_python_ok "$1" && "$1" -c "import ensurepip" >/dev/null 2>&1; }

PY_NEEDS_VENV=""
if [ -n "$PYTHON" ]; then
    case "$PYTHON" in */*) ;; *) fail "Give the full path to Python with --python, not just its name." ;; esac
    python_usable "$PYTHON" || fail "'$PYTHON' is not Python $PY_MIN_MAJOR.$PY_MIN_MINOR or newer with its venv part."
elif ! pretends python; then
    for cand in python3.11 python3.12 python3.13 python3.14 python3 python; do
        have "$cand" || continue
        if tools_skip_python "$cand"; then continue; fi
        if python_usable "$cand"; then
            PYTHON="$(command -v "$cand")"
            break
        fi
        if tools_python_ok "$cand" && [ -z "$PY_NEEDS_VENV" ]; then PY_NEEDS_VENV="$cand"; fi
    done
fi
OWN_PY="$TOOLS_DIR/python-${TOOLS_PY_VERSION}-${TOOLS_PY_BUILD}/bin/python3"
if [ -z "$PYTHON" ] && [ -x "$OWN_PY" ] && python_usable "$OWN_PY"; then PYTHON="$OWN_PY"; fi

OWN_NODE="$TOOLS_DIR/node-v${TOOLS_NODE_VERSION}-${PLATFORM}"
NEED_NODE=0
if pretends node || ! have node; then
    [ -x "$OWN_NODE/bin/node" ] || NEED_NODE=1
fi

OWN_OLLAMA="$TOOLS_DIR/ollama-${TOOLS_OLLAMA_VERSION}/ollama"
ollama_answers() {
    have curl || return 1
    curl -fsS --max-time 2 "http://127.0.0.1:11434/api/version" >/dev/null 2>&1 \
        || curl -fsS --max-time 2 "http://127.0.0.1:11435/api/version" >/dev/null 2>&1
}
NEED_OLLAMA=0
if pretends ollama || { ! have ollama && ! ollama_answers; }; then
    [ -x "$OWN_OLLAMA" ] || NEED_OLLAMA=1
fi

# A try reads an app's page with a browser, since most apps draw theirs with JavaScript.
BROWSER=0
for cand in chromium chromium-browser google-chrome google-chrome-stable microsoft-edge brave-browser; do
    if have "$cand"; then BROWSER=1; fi
done
if [ "$IS_MAC" = "1" ] && [ -d "/Applications/Google Chrome.app" ]; then BROWSER=1; fi
[ -n "$PYTHON" ] && say "  Found Python ($PYTHON)."
[ "$NEED_NODE" = "0" ] && say "  Found Node.js."
[ "$BROWSER" = "0" ] && say "  No Chromium or Chrome here. Toolsmith reads an app's page with one, so most apps can't be checked without it. Install Chromium or Chrome when you can."
[ "$NEED_OLLAMA" = "0" ] && say "  Found Ollama."

OFFER_PY=0; OFFER_NODE=0; OFFER_OLLAMA=0
if [ -z "$PYTHON" ] && tools_pin python "$PLATFORM" >/dev/null 2>&1; then OFFER_PY=1; fi
if [ "$NEED_NODE" = "1" ] && tools_pin node "$PLATFORM" >/dev/null 2>&1; then OFFER_NODE=1; fi
if [ "$NEED_OLLAMA" = "1" ] && tools_pin ollama "$PLATFORM" >/dev/null 2>&1; then OFFER_OLLAMA=1; fi
if ! tools_dir_ok "$TOOLS_DIR" "$REPO_DIR"; then OFFER_PY=0; OFFER_NODE=0; OFFER_OLLAMA=0; fi
if [ "$NO_TOOLS" = "1" ]; then OFFER_PY=0; OFFER_NODE=0; OFFER_OLLAMA=0; fi

GO_TOOLS=0
if [ "$OFFER_PY$OFFER_NODE$OFFER_OLLAMA" != "000" ]; then
    say "  This computer doesn't have:"
    [ "$OFFER_PY" = "1" ] && say "    - Python $TOOLS_PY_VERSION (about 25 MB), which BuildBot is written in"
    [ "$OFFER_NODE" = "1" ] && say "    - Node.js $TOOLS_NODE_VERSION (about 50 MB), which many apps need"
    [ "$OFFER_OLLAMA" = "1" ] && say "    - Ollama $TOOLS_OLLAMA_VERSION (about 170 MB), which runs the AI model"
    say "  Setup can download them from their makers' own websites, check that each one"
    say "  is exactly the copy BuildBot expects, and keep them in this folder:"
    say "    $TOOLS_DIR"
    say "  It doesn't need your password, and it changes nothing else on this computer."
    if [ "$YES_TOOLS" = "1" ]; then
        GO_TOOLS=1
    elif [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would ask: Download them now? (y/n)"
        GO_TOOLS=1
    elif [ "$CAN_ASK" = "1" ] && ask_yes_no "  Download them now?"; then
        GO_TOOLS=1
    else
        say "  Nothing was downloaded."
    fi
fi

if [ "$GO_TOOLS" = "1" ]; then
    if [ "$OFFER_PY" = "1" ]; then
        if tools_install_python "$TOOLS_DIR" "$PLATFORM"; then
            PYTHON="$TOOLS_PYTHON"
        else
            say "  Python could not be set up."
        fi
    fi
    if [ "$OFFER_NODE" = "1" ]; then
        tools_install_node "$TOOLS_DIR" "$PLATFORM" \
            || say "  Node.js could not be set up. BuildBot works without it, but apps that need it won't start."
    fi
    if [ "$OFFER_OLLAMA" = "1" ]; then
        tools_install_ollama "$TOOLS_DIR" "$PLATFORM" \
            || say "  Ollama could not be set up. Setup goes on, but your bot needs Ollama to answer."
    fi
fi

# Linux: Ollama's own official script (it sets up the service and the graphics card parts, so it asks for a password).
# The one place setup runs a downloaded script without a pinned hash, so it has its own question.
if [ "$NEED_OLLAMA" = "1" ] && [ "$OFFER_OLLAMA" = "0" ] && [ "$NO_TOOLS" = "0" ] && [ "$IS_MAC" = "0" ]; then
    OLLAMA_LINE="curl -fsSL https://ollama.com/install.sh | sh"
    say "  Ollama isn't on this computer. Your bot needs it to think."
    GO_OLLAMA=0
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would ask: Install Ollama now with its official script? It asks for your password. (y/n)"
        say "  [dry-run] on yes it would run: $OLLAMA_LINE"
    elif [ "$YES_OLLAMA" = "1" ]; then
        GO_OLLAMA=1
    elif [ "$CAN_ASK" = "1" ] && ask_yes_no "  Install Ollama now with its official script? It asks for your password."; then
        GO_OLLAMA=1
    fi
    if [ "$GO_OLLAMA" = "1" ] && have curl; then
        sh -c "$OLLAMA_LINE" || say "  That did not finish. Setup goes on, but your bot needs Ollama to answer."
    elif [ "$DRY_RUN" = "0" ]; then
        say "  To install it later, type:  $OLLAMA_LINE"
    fi
elif [ "$NEED_OLLAMA" = "1" ] && [ "$GO_TOOLS" = "0" ]; then
    say "  Ollama isn't on this computer, and your bot needs it to think. Get it from https://ollama.com/download"
fi

if [ -z "$PYTHON" ]; then
    if [ -n "$PY_NEEDS_VENV" ]; then
        say "  $PY_NEEDS_VENV is new enough, but its venv part is not installed. To add it on Debian or Ubuntu, type:"
        say "      sudo apt-get install python3-venv"
    else
        say "  Your turn: install Python $PY_MIN_MAJOR.$PY_MIN_MINOR or newer from https://www.python.org/downloads/"
        say "  or your computer's package manager, or run this setup again and let it download one."
    fi
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] setup would stop here, BuildBot needs Python."
        PYTHON="python3"
    else
        fail "BuildBot needs Python $PY_MIN_MAJOR.$PY_MIN_MINOR or newer."
    fi
fi
say "  Using Python: $PYTHON"

# --- 2. .venv and BuildBot's own parts ------------------------------------------
step 2 "BuildBot's own parts"
if [ "$DRY_RUN" = "1" ]; then
    say "  [dry-run] would make $VENV_DIR with $PYTHON, and install BuildBot into it."
else
    if [ -x "$VENV_PY" ]; then
        say "  Using the .venv folder from last time."
    else
        say "  Making .venv (BuildBot's own Python, next to this file)."
        "$PYTHON" -m venv "$VENV_DIR" || fail "Making .venv did not work. The lines above say why."
    fi
    say "  Getting BuildBot's parts from the Python package library. This takes a minute or two."
    "$VENV_PY" -m pip install -q --disable-pip-version-check --upgrade pip || fail "Updating pip did not work. The lines above say why."
    "$VENV_PY" -m pip install -q --disable-pip-version-check -e "$REPO_DIR" || fail "Installing BuildBot did not work. The lines above say why."
    [ -x "$PUFFPAI" ] || fail "the puffpai command was not made in .venv."
    say "  Done."
fi

# --- 3. Your bot's own folder ---------------------------------------------------
step 3 "Your bot's own folder"
# The same place as puffpai_core.paths.data_root.
if [ -n "${PUFFPAI_HOME:-}" ]; then
    BOT_HOME="$PUFFPAI_HOME"
elif [ "$IS_MAC" = "1" ]; then
    BOT_HOME="$HOME/Library/Application Support/PuffPAI"
else
    case "${XDG_DATA_HOME:-}" in
        /*) BOT_HOME="$XDG_DATA_HOME/puffpai" ;;
        *)  BOT_HOME="$HOME/.local/share/puffpai" ;;
    esac
fi
NAME="$BOT"
n=2
while [ -e "$BOT_HOME/$NAME" ]; do NAME="$BOT-$n"; n=$((n + 1)); done
if [ "$NAME" != "$BOT" ]; then
    say "  A bot named $BOT is already on this computer. It is kept exactly as it is."
    say "  This new bot is called $NAME."
fi
BOT_DIR="$BOT_HOME/$NAME"
if [ "$DRY_RUN" = "1" ]; then
    say "  [dry-run] would make your bot's folder: $BOT_DIR"
else
    mkdir -p "$BOT_DIR"
    chmod 700 "$BOT_HOME" "$BOT_DIR" 2>/dev/null || true
    say "  Your bot's folder: $BOT_DIR"
fi
export PUFFPAI_BOT="$NAME"
unset PUFFPAI_STATE || true

# --- 4. The launchers -----------------------------------------------------------
step 4 "The Start, Stop, Chat and Sign in launchers"
QUOTED_BOT="$(printf '%q' "$NAME")"
QUOTED_TOOLS="$(printf '%q' "$TOOLS_DIR")"
# With --data-dir the bot's own temporary files and caches stay there too, and so does what its apps download.
DATA_ENV=""
if [ -n "$DATA_DIR" ]; then
    QUOTED_DATA="$(printf '%q' "${DATA_DIR%/}")"
    DATA_ENV="export PUFFPAI_HOME=$QUOTED_DATA/puffpai TMPDIR=$QUOTED_DATA/tmp XDG_CACHE_HOME=$QUOTED_DATA/cache PIP_CACHE_DIR=$QUOTED_DATA/cache/pip npm_config_cache=$QUOTED_DATA/cache/npm
mkdir -p \"\$TMPDIR\" \"\$XDG_CACHE_HOME\""
fi

# D129: a launcher next to the code that belongs to another bot stays as it is: this bot's go in bots/<name>/.
LAUNCH_DIR="$REPO_DIR"
CODE_FROM_LAUNCHER='.'
if [ -f "$REPO_DIR/start-buildbot.sh" ] && ! grep -qxF "export PUFFPAI_BOT=$QUOTED_BOT" "$REPO_DIR/start-buildbot.sh"; then
    LAUNCH_DIR="$REPO_DIR/bots/$NAME"
    CODE_FROM_LAUNCHER='../..'
    if [ "$DRY_RUN" = "0" ]; then mkdir -p "$LAUNCH_DIR"; fi
fi

# write_launcher <file> <what it does, in a comment> <the last lines>
write_launcher() {
    if [ "$DRY_RUN" = "1" ]; then
        say "  [dry-run] would write $LAUNCH_DIR/$1"
        return 0
    fi
    cat > "$LAUNCH_DIR/$1" <<EOF
#!/usr/bin/env bash
# $2 ($NAME). Written by install.sh.
export PUFFPAI_BOT=$QUOTED_BOT
unset PUFFPAI_STATE
$DATA_ENV
HERE="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")/$CODE_FROM_LAUNCHER" && pwd)"
TOOLS=$QUOTED_TOOLS
for own in "\$TOOLS"/node-v*/bin "\$TOOLS"/ollama-*; do
    if [ -x "\$own/node" ] || [ -x "\$own/ollama" ]; then PATH="\$own:\$PATH"; fi
done
export PATH
cd "\$HERE"
$3
EOF
    chmod +x "$LAUNCH_DIR/$1"
    say "  Written: $LAUNCH_DIR/$1"
}

# A Mac's downloaded Ollama is a plain program with no service: the start launcher starts it, on this computer only,
# when nothing answers there yet. It has a session of its own, so closing this terminal doesn't stop it.
START_OLLAMA='for own in "$TOOLS"/ollama-*/ollama; do
    if [ -x "$own" ] && ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
        OLLAMA_HOST=127.0.0.1:11434 nohup "$own" serve >/dev/null 2>&1 </dev/null &
    fi
done'

write_launcher start-buildbot.sh "Starts BuildBot" "$START_OLLAMA
exec \"\$HERE/.venv/bin/puffpai\" start \"\$@\""
write_launcher stop-buildbot.sh "Stops BuildBot" 'exec "$HERE/.venv/bin/puffpai" shutdown "$@"'
write_launcher chat-buildbot.sh "Chat with BuildBot in this terminal" 'exec "$HERE/.venv/bin/puffpai" "$@"'
write_launcher signin-buildbot.sh "Signs a browser in to BuildBot" '"$HERE/.venv/bin/puffpai" pair --running "$@" || exit $?
echo "Open the link above in your browser on this computer. For a new link, run this again."'
if [ "$IS_MAC" = "1" ]; then
    tools_write_mac_launchers "$LAUNCH_DIR" "$LAUNCH_DIR" || say "  The Finder launchers could not be written. The .sh ones work."
fi
say "  Next time, run $LAUNCH_DIR/start-buildbot.sh: it starts your bot in the background and opens the chat here."
say "  stop-buildbot.sh next to it stops it. chat-buildbot.sh opens another chat while it runs."
say "  signin-buildbot.sh makes a new link that signs a browser in to your Desktop."

# --- 5. The built-in packages and the setup steps -------------------------------
step 5 "The system parts, then setup"
if [ "$DRY_RUN" = "1" ]; then
    say "  [dry-run] would run: $PUFFPAI setup"
    say "  Practice run done. Nothing was changed."
    exit 0
fi
if [ "$NO_SETUP" = "1" ]; then
    say "  Skipped for now (--no-setup). When you are ready, run:"
    say "      PUFFPAI_BOT=$QUOTED_BOT $PUFFPAI setup"
    say "  Then $LAUNCH_DIR/start-buildbot.sh starts your bot, and stop-buildbot.sh next to it stops it."
    exit 0
fi
say "  Each system part (the Desktop, setup, the cloud avatar) shows its Yes card first. Then setup asks a few questions."
# Setup shows PINs and a recovery code: never in the log.
exec 1>&3 2>&4
code=0
if [ -n "$DATA_DIR" ]; then
    export TMPDIR="${DATA_DIR%/}/tmp" XDG_CACHE_HOME="${DATA_DIR%/}/cache" PIP_CACHE_DIR="${DATA_DIR%/}/cache/pip" npm_config_cache="${DATA_DIR%/}/cache/npm"
    mkdir -p "$TMPDIR" "$XDG_CACHE_HOME"
else
    unset TMPDIR PIP_CACHE_DIR
fi
if [ -n "$FROM_FOLDER" ]; then
    PATH="$OWN_NODE/bin:$PATH" "$PUFFPAI" setup --from "$FROM_FOLDER" || code=$?
else
    PATH="$OWN_NODE/bin:$PATH" "$PUFFPAI" setup || code=$?
fi
say ""
if [ "$code" = "0" ]; then
    say "BuildBot is set up. It is stopped now."
else
    say "Setup is not finished yet. Run start-buildbot.sh, then open Setup in the Desktop, or run setup again."
fi
say "In $LAUNCH_DIR:"
say "  ./start-buildbot.sh starts BuildBot again."
say "  ./stop-buildbot.sh stops it, from any terminal."
say "  ./chat-buildbot.sh chats with it while it runs."
say "  ./signin-buildbot.sh signs in a browser while it runs."
exit "$code"
