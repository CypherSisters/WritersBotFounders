# PuffPAI BuildBot v2

**Your Hardware. Your Data. Your AI.**

**Build it once, own it forever.**

BuildBot makes your own AI bot on your own computer. Say what you want in your own words. Toolsmith looks for ready-made apps, books and services, tries them, and shows you one plan. You say Yes, and it builds the bot. Add more whenever you like.

The brain runs on your computer, on a model you pick. Nothing leaves your computer without your consent.

Under it is a small Core every bot shares: three zones (Vault, Brain, Bridge) and seven agents. Everything else is a package, so anyone can add apps, tools, skills, whole new screens and more. It's the same idea as Linux: a bare base anyone can build on. BuildBot is the base, and HomeBot is a full persona built on the same idea.

## What you need

- **Git**, and a Windows 10 or 11 PC, a Linux PC, a Jetson (ARM64) or a Mac.
- **Memory decides the model.** From 2 GB for the smallest, 10 GB for `qwen3.5:9b`, which handles real requests, up to 24 GB for `qwen3:30b-a3b`.
- **Python and Node.js** come with the installer if you don't have them. It lists what it would download and asks once. Nothing goes system-wide, and it needs no administrator or sudo.
- **Ollama** runs the model. If it's already running, BuildBot uses it. If not, the installer offers it: a plain copy on Windows and Mac, Ollama's own script on Linux.
- **Docker** only for apps that come as Docker apps.
- **For the window:** nothing extra on Windows. Chromium or Chrome on Linux and Mac.

## Install

```bash
git clone https://github.com/CypherSisters/BuildBotv2
cd BuildBotv2
```

**Linux and Jetson:**

```bash
./install.sh
```

On a small home disk, such as a Jetson's, add `--data-dir <folder>`. `--dry-run` shows every step and changes nothing.

**Windows:** double-click `install.cmd`, or in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File install.ps1
```

`-DryRun` shows every step and changes nothing.

**Mac:** `./install.sh`, the same as Linux. It installs, starts and stops on GitHub's Mac machines. No person has used it on a Mac yet.

How far each has been run:

| Computer | State |
|---|---|
| Windows | Where most of the live testing happened |
| Linux | Install, setup, chat and a store app, run in containers. The full test suite runs here too |
| Jetson | One real install, inside a container, on a Jetson Orin |
| Mac | Installs, starts and stops on GitHub's Mac machines (Apple silicon), and its tests pass there. Not yet: setup with a model, a real app added, the window on a screen, or a Toolsmith try. No person has used it on a Mac yet |

## First run

The installer goes straight into setup, in your terminal: the brain, a PIN for your vault, a recovery code to keep safe, your data folder, the Bridge (open or closed), and whether Toolsmith may use a cloud model. Then your goal, one plan, and one Yes.

After that, use the launchers next to the code:

| | Windows | Linux and Mac |
|---|---|---|
| Start | `Start BuildBot.cmd` | `./start-buildbot.sh` |
| Stop | `Stop BuildBot.cmd` | `./stop-buildbot.sh` |
| Chat in a terminal | `Chat with BuildBot.cmd` | `./chat-buildbot.sh` |
| Sign in a browser | `Sign in a browser.cmd` | `./signin-buildbot.sh` |

Start opens the PuffPAI window. The same bot has a dashboard in your browser at http://puffpai.localhost:8420.

**Your vault locks after 5 minutes idle.** Setup asks for your PIN in its own window if it needs it. Elsewhere, type `/unlock` in the chat.

**A cloud model is your choice.** Only Toolsmith may use one, only if you pick it, with your own key, and with a $5 cap per build by default. A try runs as a program on your computer, as you, and what it shows goes to the company you picked.

## Other devices

Any device you pair on your home network gets everything you get on the bot's computer. Devices you haven't paired see only the pair screen.

1. Setup's last screen gives the address and a code. For a new code, use the Sign in launcher, or Settings, Your home network.
2. On the other device, open the address and type the code.
3. Remove a device in Settings, Paired devices.

To run the window on another computer, with no bot on it:

```bash
./install.sh --window-for http://<address>
```

On Windows, use `install.ps1 -WindowFor http://<address>`. Turn off the home network with `puffpai network off`. `puffpai` lives in `.venv/bin/` (`.venv\Scripts\` on Windows).

## More than one bot

```bash
./install.sh --bot second
```

On Windows, use `install.ps1 -Bot second`. You're never asked for a port: each bot takes the next free set and keeps it. They share one Ollama, so one may wait while another answers. The second bot's launchers are in `bots/second/`.

## Moving a bot

`puffpai export <folder>`, or Settings, Move this bot to another computer, writes a folder you can put in a git repo. On the new computer, run `./install.sh --from <folder>` (`install.ps1 -From <folder>` on Windows).

It carries the name, the look, who your bot is, and what it came with. The vault, keys, chats, documents and pairings never go. Apps and books come back from a list on Knowledge, "Recommended for this bot", each with its own Yes.

## The store

Apps, PuffPAI store, in the window or the dashboard, or `puffpai store list`. The packages are at [CypherSisters/puffpai-store](https://github.com/CypherSisters/puffpai-store). Toolsmith can also suggest them on a plan.

## Build for it

```bash
puffpai new tool my-tool
puffpai check my-tool
puffpai add --link my-tool
```

`puffpai new` also starts an `app`, `skill`, `page`, `avatar` or `look`. A folder of your own code can run with no manifest at all. Programs can talk to your bot with `puffpai apikey new`, in OpenAI or Ollama style, and that's how Home Assistant connects. It hasn't been tried with a real Home Assistant yet.

## Read in this order

| Doc | What it answers |
|---|---|
| [docs/04_DEVELOPERS.md](docs/04_DEVELOPERS.md) | Build for PuffPAI: quick starts for every kind of package, the CLI, the API |
| [docs/02_PACKAGE_CONTRACT.md](docs/02_PACKAGE_CONTRACT.md) | `puffpai.json`, the package kinds, what "Works on PuffPAI" means |
| [docs/01_ARCHITECTURE.md](docs/01_ARCHITECTURE.md) | The Core, part by part |
| [docs/03_WIZARD_AND_TOOLSMITH.md](docs/03_WIZARD_AND_TOOLSMITH.md) | Setup, and how Toolsmith finds, adopts, makes and proves |
| [docs/09_AGENT_SECURITY.md](docs/09_AGENT_SECURITY.md) | What each agent may touch, what needs your Yes, and the honest limits |
| [docs/zones/ZONES_TOGETHER.md](docs/zones/ZONES_TOGETHER.md) | The three zones and the floor rules |
| [docs/00_INTENT_AND_DECISIONS.md](docs/00_INTENT_AND_DECISIONS.md) | Every decision, with its date. A row marked PROPOSED was written before the owner's live test of it |

AI assistants: start with [AGENTS.md](AGENTS.md). To work on the code itself: [core/README.md](core/README.md).

## Rough edges

- **No TLS on your home network.** What a paired device does travels in plain text there. It's home network only: a VPN address is turned away.
- **The window off Windows is a browser app.** It's the same page in Chromium or Chrome's app mode, as protected as a browser tab. A native window comes later. Only Windows has the locked-down window.
- **Not proved on real hardware:** a person's Mac (setup with a model, a real app, the window, a Toolsmith try), a real Linux desktop's window, and most of what crosses between two real devices. So far, one window has reached one bot.
- **Small models** give weaker answers, and may not use a skill or a kept service. Bots sharing Ollama slow each other down.
- **A program key is powerful.** It can use your bot's tools, read pinned documents and get real values while the vault is open. Key use isn't logged yet. A Toolsmith plan card doesn't yet say a program asked.
- **Removing an app** doesn't stop its Docker containers yet.
- **Not in v2 yet:** voice, routines and reminders, making an app from found code, pictures and Excel in knowledge, meaning search, and a Smart Home page.
- **No upgrade from v1.** Build again with v2. An upgrade path that keeps your bot is required from v3.

## v1

v1 lives on at [CypherSisters/PuffPAIBuildBot](https://github.com/CypherSisters/PuffPAIBuildBot).

## License

Apache 2.0: [LICENSE](LICENSE). What comes from others: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Cypher Sisters. puff puff pass.
