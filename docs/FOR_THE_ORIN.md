# WritersBot on the Orin: the long form

The short form is `START_HERE.md`. This page says what the script does, so you can do any step by hand, and it covers
the picture program and life with two bots on one Orin. Written against a Jetson AGX Orin on JetPack 6.2 (Ubuntu
22.04, Python 3.10 on the host, CUDA 12.6, 64 GB shared memory), which is the same board the bot was built on.

## 1. What WritersBotFounders is

Exactly the box it was built on: **one Docker container** (`writersbot-box`) from the image the recipe builds
(`BuildBotv2/scripts/container/Dockerfile`: Debian, Python 3.12, Chromium, Node, socat), running as uid 1000 on
Docker's bridge network, with 8 GB of memory and 4 CPUs, no sudo inside, no `docker.sock`, no `--privileged`, no host
networking. Everything the bot keeps lives on one mount, the data folder (`~/puffpai/writersbot` by default, seen
as `/data/bot` inside). That includes My files and every manuscript, in `<DATA>/PuffPAI Data/writersbot`. The brain is the Ollama already on the Orin host, reached through a small relay inside the
container. Removing the container and the data folder removes everything.

The BuildBot code is the copy in `BuildBotv2/`, WritersBot's own version, frozen. It is not updated from BuildBot's
main; `docs/FOR_HER_CLAUDE.md` lists its differences.

## 2. Before the script

Run the script as yourself, with no sudo. Under sudo it says so and goes on as you; as plain root it stops.

- **Docker** works for your user: `docker info` answers. On JetPack it is installed; `sudo usermod -aG docker $USER`
  then log out and in if it refuses you.
- **Ollama** is installed on the host (`curl -fsSL https://ollama.com/install.sh | sh`). The script checks it at the
  address the container uses: Docker's bridge address, normally `172.17.0.1:11434` (that is the host as the container
  sees it). If Ollama answers only on 127.0.0.1 and runs as a service, the script writes
  `/etc/systemd/system/ollama.service.d/writersbot.conf` with `[Service]` and `Environment="OLLAMA_HOST=0.0.0.0:11434"`,
  restarts it and checks again. **That makes Ollama reachable on your home network with no password.** If it still does
  not answer, or there is no service, it prints the exact steps and stops before building. This test runs from the
  host, so a firewall on Docker's network passes it. That shows only after the build, when the container tests Ollama
  from inside; the script then prints what to do (`sudo ufw allow from 172.16.0.0/12 to any port 11434` if ufw is
  active, otherwise check iptables), and you run it again.
- **The model** the bot was built with: if `qwen3:30b-a3b` is missing, the script offers `ollama pull qwen3:30b-a3b`
  (about 19 GB). Say no and it goes on; pull it later. A smaller model works, with thinner readings.
- **The data folder** is `~/puffpai/writersbot`. Another folder is fine: run the script with `DATA=/your/folder`. The
  container runs as uid 1000, so if the folder belongs to anyone else, the script runs `sudo chown -R 1000:1000` on it
  and says so. `start`, `stop`, `pair`, `chat`, `setup` and `update` find the folder from the container, so you need not repeat it.
- **Ports** 8423 and 19100-19120 free on the host for this bot. If something else holds one, the script names it if it
  can, and stops. A second PuffPAI bot in a container on this Orin uses its own set; see section 6.

## 3. What `install-writersbot.sh` does, step by step

1. The checks in section 2, before anything slow. A container left from an earlier try that points at another data
   folder is removed, with a line saying so; nothing lives in it.
2. `run.sh build`: builds the image `puffpai-box` from the Dockerfile. A few minutes of technical lines the first time.
3. `run.sh install`: starts the container (or makes it), starts the Ollama relay inside, copies the BuildBot code into
   `/data/bot/code`, and runs BuildBot's `install.sh --data-dir /data/bot --bot writersbot --no-tools --no-setup` there:
   a `.venv`, the `puffpai` command. If `<DATA>/code/.venv/bin/puffpai` is there already, the script skips this and only
   runs `run.sh up` (the container and the relay, nothing else). A second install would make a second bot. `run.sh install`
   keeps a marker, `<DATA>/.installing`, until it succeeds. Ctrl+C leaves the install running inside the container, so
   if the script finds that marker, it restarts the container, which ends only what runs in there, clears
   `/data/bot/code` and `/data/bot/puffpai` (setup has never run, so nothing of yours is in them), and installs again.
4. Copies the bundle `bot/` to `<DATA>/from/writersbot`.
5. With `--pictures`: runs `install-pictures.sh` on the host (section 5).
6. `puffpai setup --from /data/bot/from/writersbot` inside the container, in your terminal. The bundle gives the bot
   its name (Dewey), look, role, Home and apps; setup never asks what the bot is for. It shows the Yes cards for the
   system parts, then asks about the model, the vault and its PIN, the Bridge, and Toolsmith's model. If setup has
   finished already (`"complete": true` in `settings/wizard.json` in the bot's folder), the script skips steps 4 and 6
   and prints the everyday commands. If it has not, `start`, `pair` and `chat` run setup first.

Then, still in setup and still in that terminal, each app in the bundle shows its own Yes card: Manuscript, Editor's
Desk, Maps, Novel mode (text only), Calculator, Document convert, Document search and World Radio. The bot's main apps download what
they need while they install, so if the Bridge is closed, setup first asks to open it for this. Press Enter for Yes. With
a plain No the Bridge stays closed and Manuscript does not install; setup says so and names the command to try again.
Type `y` on each card; Enter alone means No there. An app you say No to is not added.

If setup is cut off, `./install-writersbot.sh setup` goes on from there, stopping the bot first if it is running. Once
setup has finished, the way back for an app is `./install-writersbot.sh update`, which keeps the name, the look and
everything you changed, and adds back an app that is missing or broken, keeping its data. `START_HERE.md` lists every
question with what to answer.

When setup is done it says so, and the terminal goes on as the running bot and a chat. Ctrl+C stops the bot. From then
on `./install-writersbot.sh start` runs it in the background and `stop` stops it. The vault locks after five minutes
idle; the window shows a PIN box at the top when it does.

## 4. Everyday

| What | Command, from `~/WritersBotFounders` |
|---|---|
| Start the bot in the background | `./install-writersbot.sh start` (it opens a chat; leaving the chat does not stop the bot) |
| Stop the bot and the container | `./install-writersbot.sh stop` |
| A sign-in code for the window or a browser | `./install-writersbot.sh pair` |
| Another chat in a terminal | `./install-writersbot.sh chat` |
| Go on with setup that was cut off | `./install-writersbot.sh setup` |
| Add again an app that is missing or broken | `./install-writersbot.sh update` |
| Yes cards from a terminal | `docker exec -it writersbot-box bash -c "cd /data/bot/code; .venv/bin/puffpai turn"` then `... puffpai yes <id>` |
| Restart the Core without a terminal | `docker exec -d writersbot-box bash -c "cd /data/bot/code && exec .venv/bin/puffpai serve >>/data/bot/puffpai/writersbot/logs/serve.out 2>&1"` then reload the window |
| Bring the bot's Core, window and own apps up to this copy | `./install-writersbot.sh update` (see below) |
| Change the memory cap | stop, `docker rm writersbot-box`, run with `MEMORY=12g ./install-writersbot.sh start`, adding `DATA=/your/folder` if you use your own (the bot lives on the mount; nothing is lost) |

**`./install-writersbot.sh update`** is for a bot already set up, after you get the newest version (section 10). Run it
at a terminal. First it brings the Core and the window up to this copy, if they differ. Then it puts the bundle's list of
books and articles on Knowledge, under Recommended for this bot, where they wait for a Yes. Then it copies this folder's
bundle to `<DATA>/from/writersbot` and brings Novel mode, Manuscript, Editor's Desk, Maps and Document convert forward, in
that order, each with its own Yes card here. Type `y` on each. Editor's Desk is new: on a bot from before it, `update` adds it,
and it downloads nothing, so it needs no Bridge.

- For the Core and the window it says so in one line, waits if Manuscript is reading a book, and keeps a copy of what was
  there at `<DATA>/before-update.tar`. It restarts the Core process, never the container, so a bot that keeps its books
  in the container is safe. If the new Core does not answer within two minutes, it puts the old one back and says so.
- A restart locks the vault. If the window shows a PIN box at the top, type your PIN there. Reload the window once (F5).

- An app already at the bundle's version is skipped with one line. An app that is missing is added, and one that is broken
  is taken out and added again keeping its data, each with its own Yes card.
- An update restarts the app, so if Manuscript is reading a book, it stops before changing anything and says why.
- Manuscript and Document convert download what they need, so the Bridge has to be open. If it is closed, the script
  asks to open it for this, and closes it again afterward.
- Document convert on a bot from the first bundle came from the store. It is taken out, keeping its data, and added
  again from the bundle. Say No on its card and it stays out until you run this again.
- If the bot is not running, the script starts it in the background and leaves it running.
- It does not touch the vault, the settings, the chats, the name, the look, the role, the manuscripts or the notes. Books kept
  inside the container (section 8) stay there; it says so.
- Afterward, reload the window once (F5).

## 5. Pictures: the picture program on this Orin

Maps paints portraits, places, a painted world map, scenes and a cover through ComfyUI. On this setup it runs on the
Orin host, beside the container, and Maps reaches it at the Orin's own home-network address on port 8188.

`install-pictures.sh` does, as the person at the Orin would by hand:

1. A Python venv at `~/comfy-venv`; `pip install "torch==2.8.0" "torchvision==0.23.0" --index-url https://pypi.jetson-ai-lab.io/jp6/cu126`
   (NVIDIA's Jetson wheels for JetPack 6 and CUDA 12.6). Torch is pinned to 2.8.0 because the newest there, 2.11, needs
   `libcudss.so.0`, which no JetPack r36.4 package supplies, so it would not load. The script first checks that CUDA 12
   is installed (if not: `sudo apt install nvidia-jetpack`), then prints the torch version, its CUDA and whether the chip
   is usable. If torch won't load or the chip isn't usable, it stops before downloading any model.
2. `git clone` ComfyUI to `~/ComfyUI`, its requirements, and the `ComfyUI-GGUF` add-on into `custom_nodes`.
3. A model: **SDXL Turbo** by default (`sd_xl_turbo_1.0_fp16.safetensors`, 6.9 GB, into `models/checkpoints`; under a
   minute a picture on an Orin), or with `--flux` **FLUX schnell** (four files, about 12 GB, into `unet`, `clip` and
   `vae`; one to two minutes a picture, prettier). `--both` gets both; Maps prefers FLUX when both are there. Each file
   downloads as `<name>.part` and takes its name only when finished, so running the script again goes on from where a
   download stopped.
4. A user service `comfyui.service` that starts it with the Orin, listening on `0.0.0.0:8188`, and lingering so it runs
   without a login. If lingering can't be turned on, it says it won't start at boot until you log in once.
5. Prints the address to type into Maps.

Then, once, in the window: Maps, the **Picture maker** box, type `http://<the Orin's address>:8188` (not 127.0.0.1:
Maps accepts home-network addresses only, and the Orin's own is one), press Test, say Yes on the Bridge card. Open
Pictures for a manuscript, or press Illustrate. If Maps' Test can't reach port 8188, check the Orin's firewall.

**Not yet run on a real Orin.** The script was checked on an emulated Jetson image and the model files by hand. It is
still unproven on real Jetson hardware. If a step fails, keep its exact words.

## 6. Two bots on one Orin

Each PuffPAI bot has its own folder and its own ports: the Core at 8420, 8430, 8440 and so on, its home-network
listener at the Core port + 3, its app pages from 19100 + 100 per bot. The container recipe publishes 8423 and
19100-19120 for the first bot. If the other bot already holds those on the host, run this one with
`PUBLISH="-p 8433:8423 -p 19200-19220:19100-19120"` and give the writer `http://<orin>:8433`. (The bot inside still
thinks it is the first bot; only the host side moves.) Both bots share the chip and, with the same model name, the
loaded model; a different large model on the other bot means reloads and slower turns.

The picture program is one per Orin; a second bot's Maps points at the same address.

## 7. Removing it

`./install-writersbot.sh stop`, `docker rm -f writersbot-box`, `docker rmi puffpai-box`, delete the data folder with
`sudo rm -rf ~/puffpai/writersbot` (or your own `DATA`; its files belong to uid 1000). For pictures:
`systemctl --user disable --now comfyui.service`, delete `~/ComfyUI` and `~/comfy-venv`. If the script made Ollama listen
wider and nothing else needs that: `sudo rm /etc/systemd/system/ollama.service.d/writersbot.conf`, then
`sudo systemctl daemon-reload && sudo systemctl restart ollama`.

## 8. If an earlier try went wrong

Do only the parts that happened to you, then run `./install-writersbot.sh` again, with no sudo. The script handles a
stale container and a wrong owner by itself; these commands are for clearing things away by hand. Nothing here touches
Ollama, its models, or the image. A bot that finished setup before its data folder moved to the mount keeps its files
inside the container; see `docs/FOR_HER_CLAUDE.md` before removing one.

**Backing up a bot whose books are inside the container.** A bot set up by the first version keeps My files and every
manuscript at `/home/owner/PuffPAI Data` in the container. They are safe across stop, start and reboot, and the script
never removes such a container. Only `docker rm` loses them. To copy them out, with the container running or stopped:

```bash
docker cp "writersbot-box:/home/owner/PuffPAI Data" ~/puffpai-data-backup
```

**A container left over from the try:**

```bash
docker rm -f writersbot-box
```

**A folder made by running the script with sudo**, when it used `/data/puffpai/writersbot`. If setup never finished there,
nothing of yours is in it:

```bash
sudo rm -rf /data/puffpai/writersbot
sudo rmdir /data/puffpai /data    # removes them only if empty; "not empty" is fine
```

**A BuildBot on the Orin itself, from running `BuildBotv2/install.sh` by hand.** If setup is still open in a terminal,
press Ctrl+C there first. Then:

```bash
cd ~/WritersBotFounders/BuildBotv2
./stop-buildbot.sh                # "not running" is fine
grep -h '^export PUFFPAI_BOT=' start-buildbot.sh bots/*/start-buildbot.sh 2>/dev/null
```

That last line names each bot this folder made, usually `buildbot`. Each one has a folder of its own under
`~/.local/share/puffpai` (under `$XDG_DATA_HOME/puffpai` if you set that). Delete only the ones it named. Any other
folder there is another bot; leave it. If you made a vault in that bot, its vault goes too. If it printed
`PUFFPAI_BOT=buildbot` (for any other name, change the last word of the first line):

```bash
rm -rf ~/.local/share/puffpai/buildbot
rm -rf .venv bots install-log.txt start-buildbot.sh stop-buildbot.sh chat-buildbot.sh signin-buildbot.sh
```

If you let it download Python or Node.js, and `ls ~/.local/share/puffpai` now shows no other bot, those go too:

```bash
rm -rf ~/.local/share/puffpai-tools
```

## 9. If the Orin's home-network address changed

The bot answers only at the address its container was made with. After a router gives the Orin a new one, a browser or
window at the new address gets an error (HTTP 421), and the old address no longer reaches the Orin. `start`, `pair` and a
full run of the script notice this.

- **The bot keeps everything on the data folder (the usual case):** the script says so, stops the bot, and makes the
  container again with the new address. Nothing is lost. After `pair`, run `start`, because the bot stopped.
- **The books are inside the container (section 8):** the script leaves it alone, says so, and goes on. Have your router
  give the Orin its old address again, and nothing else has to change. Or, to use the new address until the bot next
  stops (after a reboot, do this again), with the container running:

  ```bash
  docker exec writersbot-box bash -c "cd /data/bot/code && .venv/bin/puffpai shutdown"
  docker exec -d -e BUILDBOT_NETWORK_ADDRESSES=<the new address> writersbot-box bash -c "cd /data/bot/code && exec .venv/bin/puffpai serve >>/data/bot/puffpai/writersbot/logs/serve.out 2>&1"
  ```

  `pair` still prints the old address then; its code works at the new one.

With a new address, the writer's window shortcut needs it too: run the `-WindowFor` command in
`docs/FOR_YOUR_WINDOWS_COMPUTER.md`, Part 1, with the new address, and type a fresh `pair` code.

## 10. Getting the newest version

```bash
cd ~/WritersBotFounders && git fetch origin && git reset --hard origin/main
```

This replaces only this copy of the instructions and code. The bot and the books are not in this folder: they live in
the data folder and the container. What `BuildBotv2/install.sh` by hand may have left here (`.venv`, `bots`, the launchers)
is not part of the copy, so it stays too. A plain `git pull` can refuse, because the first version's scripts needed
`chmod +x`, and git counts that as a local change. Then, for a bot already set up: `./install-writersbot.sh update`.
