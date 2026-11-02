#!/usr/bin/env bash
# The picture program for Maps, on this computer: ComfyUI in its own Python, with NVIDIA's Jetson PyTorch on a Jetson
# (JetPack 6, CUDA 12.6) or the ordinary CUDA PyTorch on a PC with an NVIDIA card. Then a model, and a service so it
# starts with the computer. Maps reaches it at this computer's home-network address on port 8188.
#
#   ./install-pictures.sh                  SDXL Turbo (one file, about 7 GB; the faster one)
#   ./install-pictures.sh --flux           FLUX schnell instead (four files, about 12 GB; the prettier one, slower)
#   ./install-pictures.sh --both
#
# Downloads come from huggingface.co and github.com. Nothing here touches WritersBot's own files.
set -euo pipefail

# Not as root: the program, its model and its service belong in your home folder.
if [ "$(id -u)" = 0 ]; then
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
        echo "This script does not need sudo. Going on as $SUDO_USER."
        exec sudo -u "$SUDO_USER" -H bash "$(readlink -f "${BASH_SOURCE[0]}")" "$@"
    fi
    echo "Do not run this as root. Log in as yourself and run it again, with no sudo."
    exit 1
fi

WANT_SDXL=1; WANT_FLUX=0
for a in "$@"; do
    case "$a" in
        --flux) WANT_SDXL=0; WANT_FLUX=1 ;;
        --both) WANT_SDXL=1; WANT_FLUX=1 ;;
        -h|--help) sed -n 2,10p "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Unknown option: $a"; exit 1 ;;
    esac
done
say() { printf '\n== %s\n' "$*"; }
COMFY="$HOME/ComfyUI"; VENV="$HOME/comfy-venv"; PORT=8188

say "Python for the picture program"
PY="$(command -v python3.10 || command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 is missing."; exit 1; }
sudo -v || { echo "This step needs your password for sudo. Run it at a terminal, or over ssh with: ssh -t"; exit 1; }
sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv python3-pip git libopenblas-dev aria2 >/dev/null
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1090
source "$VENV/bin/activate"
pip install -q --upgrade pip

say "PyTorch"
if [ -e /etc/nv_tegra_release ]; then
    # Not grep -q: it ends early, and pipefail then counts the cut-off ldconfig as a failure.
    if ! PATH="$PATH:/sbin:/usr/sbin" ldconfig -p | grep libcudart.so.12 >/dev/null; then
        echo "CUDA 12.6 is not installed. Run: sudo apt install nvidia-jetpack"
        exit 1
    fi
    echo "  Jetson found. Using NVIDIA's Jetson wheel index for JetPack 6 / CUDA 12.6."
    # Pinned: the index's newest torch, 2.11, links libcudss.so.0, which no JetPack r36.4 package supplies, so it won't load.
    pip install -q "torch==2.8.0" "torchvision==0.23.0" --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
    if ! python - <<'EOF'
import sys
try:
    import torch
except Exception as exc:
    print("  ", exc)
    sys.exit(1)
ok = torch.cuda.is_available()
print("  torch", torch.__version__, "| CUDA", torch.version.cuda, "| chip usable:", ok)
sys.exit(0 if ok else 1)
EOF
    then
        echo "PyTorch can't use the chip on this Jetson, so no model was downloaded. Check JetPack with: cat /etc/nv_tegra_release"
        exit 1
    fi
else
    echo "  Not a Jetson. Using the ordinary CUDA build of PyTorch."
    pip install -q torch torchvision
    python - <<'EOF' || echo "  PyTorch did not load. Going on; pictures may not work on this computer."
import torch
ok = torch.cuda.is_available()
print("  torch", torch.__version__, "| chip usable:", ok)
if not ok:
    print("  No usable chip from this PyTorch. Pictures would be very slow on the CPU.")
EOF
fi

say "ComfyUI and the GGUF add-on"
if [ -d "$COMFY/.git" ]; then git -C "$COMFY" pull -q --ff-only || true; else git clone -q https://github.com/comfyanonymous/ComfyUI.git "$COMFY"; fi
pip install -q -r "$COMFY/requirements.txt"
if [ -d "$COMFY/custom_nodes/ComfyUI-GGUF/.git" ]; then git -C "$COMFY/custom_nodes/ComfyUI-GGUF" pull -q --ff-only || true; else git clone -q https://github.com/city96/ComfyUI-GGUF.git "$COMFY/custom_nodes/ComfyUI-GGUF"; fi
pip install -q -r "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt"

get() {  # get <url> <folder> <file>
    local dir="$COMFY/models/$2" part="$3.part"
    mkdir -p "$dir"
    # A control file beside it means an earlier aria2 download stopped part way.
    if [ -s "$dir/$3" ] && [ ! -e "$dir/$3.aria2" ]; then echo "  have $2/$3"; return 0; fi
    echo "  downloading $2/$3"
    if ! aria2c -q -c -x 8 -s 8 -d "$dir" -o "$part" "$1"; then
        # With a control file the .part has holes in it, and curl can only go on from a file with none.
        if [ -e "$dir/$part.aria2" ]; then rm -f "$dir/$part" "$dir/$part.aria2"; fi
        curl -fL --retry 3 -C - -o "$dir/$part" "$1"
    fi
    mv "$dir/$part" "$dir/$3"
    rm -f "$dir/$part.aria2" "$dir/$3.aria2"
}
HF=https://huggingface.co
if [ "$WANT_SDXL" = 1 ]; then
    say "SDXL Turbo (about 7 GB)"
    get "$HF/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors" checkpoints sd_xl_turbo_1.0_fp16.safetensors
fi
if [ "$WANT_FLUX" = 1 ]; then
    say "FLUX schnell (about 12 GB in four files)"
    get "$HF/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q4_K_S.gguf" unet flux1-schnell-Q4_K_S.gguf
    get "$HF/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors" clip t5xxl_fp8_e4m3fn.safetensors
    get "$HF/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors" clip clip_l.safetensors
    get "$HF/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors" vae ae.safetensors
fi

say "A service, so it starts with this computer"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/comfyui.service" <<EOF
[Unit]
Description=ComfyUI, the picture program for Maps
After=network-online.target

[Service]
WorkingDirectory=$COMFY
ExecStart=$VENV/bin/python main.py --listen 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
# First, so the user's service manager is there even with no desktop session.
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" || true
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)" != yes ]; then
    echo "  It won't start when this computer boots until you log in once."
fi
for _ in 1 2 3 4 5; do
    if [ -S "$XDG_RUNTIME_DIR/bus" ]; then break; fi
    sleep 1
done
systemctl --user daemon-reload
systemctl --user enable --now comfyui.service
sleep 8
# The address this computer really uses to go out. hostname -I gives every network, in no promised order.
ADDR="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' || true)"
if [ -z "$ADDR" ]; then ADDR="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"; fi
if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/system_stats" >/dev/null; then
    say "The picture program is running."
    echo "  In Maps, in the Picture maker box, type:  http://$ADDR:$PORT   then Test, then say Yes on the Bridge card."
    echo "  Use that address, not 127.0.0.1: Maps only accepts home-network addresses, and this computer's own address is one."
else
    echo "  It did not answer yet. Check: systemctl --user status comfyui.service"
fi
