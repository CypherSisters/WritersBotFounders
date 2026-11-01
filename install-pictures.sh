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
PY="$(command -v python3.10 || command -v python3)"
[ -n "$PY" ] || { echo "python3 is missing."; exit 1; }
sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv python3-pip git libopenblas-dev aria2 >/dev/null
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1090
source "$VENV/bin/activate"
pip install -q --upgrade pip

say "PyTorch"
if [ -e /etc/nv_tegra_release ]; then
    echo "  Jetson found. Using NVIDIA's Jetson wheel index for JetPack 6 / CUDA 12.6."
    pip install -q torch torchvision --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
else
    echo "  Not a Jetson. Using the ordinary CUDA build of PyTorch."
    pip install -q torch torchvision
fi
python - <<'EOF'
import torch
ok = torch.cuda.is_available()
print("  torch", torch.__version__, "| chip usable:", ok)
if not ok:
    print("  The chip is not usable from this PyTorch. On a Jetson, check that the wheel index matches JetPack (cat /etc/nv_tegra_release). Pictures would be very slow on the CPU.")
EOF

say "ComfyUI and the GGUF add-on"
if [ -d "$COMFY/.git" ]; then git -C "$COMFY" pull -q --ff-only || true; else git clone -q https://github.com/comfyanonymous/ComfyUI.git "$COMFY"; fi
pip install -q -r "$COMFY/requirements.txt"
if [ -d "$COMFY/custom_nodes/ComfyUI-GGUF/.git" ]; then git -C "$COMFY/custom_nodes/ComfyUI-GGUF" pull -q --ff-only || true; else git clone -q https://github.com/city96/ComfyUI-GGUF.git "$COMFY/custom_nodes/ComfyUI-GGUF"; fi
pip install -q -r "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt"

get() {  # get <url> <folder> <file>
    mkdir -p "$COMFY/models/$2"
    if [ -s "$COMFY/models/$2/$3" ]; then echo "  have $2/$3"; return 0; fi
    echo "  downloading $2/$3"
    aria2c -q -x 8 -s 8 -d "$COMFY/models/$2" -o "$3" "$1" || curl -fL --retry 3 -o "$COMFY/models/$2/$3" "$1"
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
    get "$HF/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors" vae ae.safetensors
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
systemctl --user daemon-reload
systemctl --user enable --now comfyui.service
loginctl enable-linger "$USER" 2>/dev/null || true
sleep 8
ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/system_stats" >/dev/null; then
    say "The picture program is running."
    echo "  In Maps, in the Picture maker box, type:  http://$ADDR:$PORT   then Test, then say Yes on the Bridge card."
    echo "  Use that address, not 127.0.0.1: Maps only accepts home-network addresses, and this computer's own address is one."
else
    echo "  It did not answer yet. Check: systemctl --user status comfyui.service"
fi
