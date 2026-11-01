# WritersBot on the Orin: the long form

The short form is `START_HERE.md`. This page says what the script does, so you can do any step by hand, and it covers
the picture program and life with two bots on one Orin. Written against a Jetson AGX Orin on JetPack 6.2 (Ubuntu
22.04, Python 3.10 on the host, CUDA 12.6, 64 GB shared memory), which is the same board the bot was built on.

## 1. What WritersBotFounders is

Exactly the box it was built on: **one Docker container** (`writersbot-box`) from the image the recipe builds
(`BuildBotv2/scripts/container/Dockerfile`: Debian, Python 3.12, Chromium, Node, socat), running as uid 1000 on
Docker's bridge network, with 8 GB of memory and 4 CPUs, no sudo inside, no `docker.sock`, no `--privileged`, no host
networking. Everything the bot keeps lives on one mount, the data folder (`/data/puffpai/writersbot` by default, seen
as `/data/bot` inside). The brain is the Ollama already on the Orin host, reached through a small relay inside the
container. Removing the container and the data folder removes everything.

The BuildBot code is the copy in `BuildBotv2/`, WritersBot's own version, frozen. It is not updated from BuildBot's
main; `docs/FOR_HER_CLAUDE.md` lists its differences.

## 2. Before the script

- **Docker** works for your user: `docker info` answers. On JetPack it is installed; `sudo usermod -aG docker $USER`
  then log out and in if it refuses you.
- **Ollama** runs on the host and listens for containers. In its service (`systemctl edit ollama`), under `[Service]`,
  `Environment="OLLAMA_HOST=0.0.0.0"`, then `sudo systemctl restart ollama`. Check from the host:
  `curl http://172.17.0.1:11434/api/version` (that is the host as the container sees it). Pull the model the bot was
  built with: `ollama pull qwen3:30b-a3b` (about 19 GB). A smaller model works, with thinner readings.
- **A data folder** uid 1000 can write: `sudo mkdir -p /data/puffpai/writersbot && sudo chown 1000:1000 /data/puffpai/writersbot`.
  Another folder is fine: run the script with `DATA=/your/folder`.
- **Ports** 8423 and 19100-19120 free on the host for this bot. A second PuffPAI bot in a container on this Orin uses
  its own set; see section 6.

## 3. What `install-writersbot.sh` does, step by step

1. `run.sh build`: builds the image `puffpai-box` from the Dockerfile. A few minutes the first time.
2. `run.sh install`: starts the container (or makes it), starts the Ollama relay inside, copies the BuildBot code into
   `/data/bot/code`, and runs BuildBot's `install.sh --data-dir /data/bot --bot writersbot --no-tools --no-setup` there:
   a `.venv`, the `puffpai` command, the system parts. Never run this a second time on an installed bot.
3. Copies the bundle `bot/` to `<DATA>/from/writersbot`.
4. With `--pictures`: runs `install-pictures.sh` on the host (section 5).
5. `puffpai setup --from /data/bot/from/writersbot` inside the container, in your terminal. The bundle gives the bot
   its name (Dewey), look, role, Home and apps; setup asks only about the model, the vault and its PIN, the Bridge, and
   Toolsmith's model. When it says BuildBot is running, it prints the home-network address. Setup stays in that
   terminal as the running bot; Ctrl+C stops the bot. From then on `./install-writersbot.sh start` runs it in the
   background and `stop` stops it.

Every app in the bundle asks its own Yes card the first time the window or the Desktop opens: Manuscript, Maps,
Novel mode (text only), and from the store, Calculator, Document convert, Document search and World Radio. Say Yes
to each. The vault locks after five minutes idle; the window shows a PIN box at the top when it does.

## 4. Everyday

| What | Command, from `~/WritersBotFounders` |
|---|---|
| Start the bot in the background | `./install-writersbot.sh start` (it opens a chat; leaving the chat does not stop the bot) |
| Stop the bot and the container | `./install-writersbot.sh stop` |
| A sign-in code for the window or a browser | `./install-writersbot.sh pair` |
| Another chat in a terminal | `./install-writersbot.sh chat` |
| Yes cards from a terminal | `docker exec -it writersbot-box bash -c "cd /data/bot/code; .venv/bin/puffpai turn"` then `... puffpai yes <id>` |
| Restart the Core without a terminal | `docker exec -d writersbot-box bash -c "cd /data/bot/code && exec .venv/bin/puffpai serve >>/data/bot/puffpai/writersbot/logs/serve.out 2>&1"` then reload the window |
| Change the memory cap | stop, `docker rm writersbot-box`, run with `MEMORY=12g ./install-writersbot.sh start` (the bot lives on the mount; nothing is lost) |

## 5. Pictures: the picture program on this Orin

Maps paints portraits, places, a painted world map, scenes and a cover through ComfyUI. On this setup it runs on the
Orin host, beside the container, and Maps reaches it at the Orin's own home-network address on port 8188.

`install-pictures.sh` does, as the person at the Orin would by hand:

1. A Python venv at `~/comfy-venv`; `pip install torch torchvision --index-url https://pypi.jetson-ai-lab.io/jp6/cu126`
   (NVIDIA's Jetson wheels for JetPack 6 and CUDA 12.6; the index lists torch 2.11 for Python 3.10 on aarch64). It
   prints whether the chip is usable; it must say True.
2. `git clone` ComfyUI to `~/ComfyUI`, its requirements, and the `ComfyUI-GGUF` add-on into `custom_nodes`.
3. A model: **SDXL Turbo** by default (`sd_xl_turbo_1.0_fp16.safetensors`, 6.9 GB, into `models/checkpoints`; under a
   minute a picture on an Orin), or with `--flux` **FLUX schnell** (four files, about 12 GB, into `unet`, `clip` and
   `vae`; one to two minutes a picture, prettier). `--both` gets both; Maps prefers FLUX when both are there.
4. A user service `comfyui.service` that starts it with the Orin, listening on `0.0.0.0:8188`, and lingering so it runs
   without a login.
5. Prints the address to type into Maps.

Then, once, in the window: Maps, the **Picture maker** box, type `http://<the Orin's address>:8188` (not 127.0.0.1:
Maps accepts home-network addresses only, and the Orin's own is one), press Test, say Yes on the Bridge card. Open
Pictures for a manuscript, or press Illustrate.

**Not yet run on a real Orin.** The index and the model files were checked by hand; the script was not run on a
Jetson. If a step fails, keep its exact words.

## 6. Two bots on one Orin

Each PuffPAI bot has its own folder and its own ports: the Core at 8420, 8430, 8440 and so on, its home-network
listener at the Core port + 3, its app pages from 19100 + 100 per bot. The container recipe publishes 8423 and
19100-19120 for the first bot. If the other bot already holds those on the host, run this one with
`PUBLISH="-p 8433:8423 -p 19200-19220:19100-19120"` and give the writer `http://<orin>:8433`. (The bot inside still
thinks it is the first bot; only the host side moves.) Both bots share the chip and, with the same model name, the
loaded model; a different large model on the other bot means reloads and slower turns.

The picture program is one per Orin; a second bot's Maps points at the same address.

## 7. Removing it

`./install-writersbot.sh stop`, `docker rm -f writersbot-box`, `docker rmi puffpai-box`, delete the data folder. For
pictures: `systemctl --user disable --now comfyui.service`, delete `~/ComfyUI` and `~/comfy-venv`.
