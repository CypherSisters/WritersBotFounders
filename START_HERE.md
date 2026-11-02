# Start here

Three steps. The first two are for whoever sits at the Orin; the third is for the writer's Windows computer.

**1. On the Orin** (it needs Docker and Ollama; nothing else has to be set up first):

```bash
git clone https://github.com/CypherSisters/WritersBotFounders.git ~/WritersBotFounders
cd ~/WritersBotFounders
./install-writersbot.sh --pictures
```

**No sudo on the command.** Run it as yourself. If it has to change something outside your home folder, it asks for
your password by itself. **Do not run `BuildBotv2/install.sh`.** That is the plain BuildBot, which would ask what the
bot is for; WritersBot arrives whole. (If you run it anyway, it hands over to this script.)

First it checks that the container can reach Ollama. If Ollama only listens on this computer, it says so and changes
that, which makes Ollama reachable on your home network with no password. If the model `qwen3:30b-a3b` is missing, it
offers to get it (about 19 GB).

Then it builds the container, installs the bot from the bundle into `~/puffpai/writersbot`, sets up the picture program
on this Orin, and runs setup. Before setup it prints the home-network address, like `http://10.0.0.25:8423`. Keep that.

Setup asks these, in this order, all in this terminal:

| It asks | What to answer |
|---|---|
| `Add PuffPAI Desktop?`, `Add BuildBot setup?`, `Add Puff?`, each `Say Yes to this? [y/N]` | Type `y` for each. Enter alone means No. |
| `Which brain should your bot use?` | The number for `qwen3:30b-a3b`. If it is not listed, pick `Use another model` and type that name. |
| `Where should the vault live?` | Press Enter. |
| `Pick a PIN for your vault`, then again | At least 8 characters. Nothing shows as you type. |
| The recovery code | **Write it on paper.** It is shown once. Then type it back. |
| `Your data folder`, `Where should books and downloads go?`, `Link folders you already have` | Press Enter for each. They go on the data folder. |
| `While Toolsmith builds, may it search?` | Press Enter. This is the first Bridge question. It only lets Toolsmith search for new apps; the Bridge stays closed. |
| `Which model should Toolsmith use?` | Press Enter (this computer). |
| `Your GitHub key (optional)` | Press Enter to skip. |
| `This bot's main apps can't install while the Bridge is closed ... Open it for this? [Y/n]` | Press Enter for Yes. This is the second Bridge question, and it is the one that matters: with No, Manuscript does not install. The Bridge stays open afterward; you can close it on the Bridge page. |
| `Add ...?` for each app, `Say Yes to this? [y/N]` | Type `y` for each. Enter alone means No. |
| `[Enter] open the window   [d] open the dashboard   [t] chat here` | Type `t`. The window can't open on the Orin; it lives on the Windows computer (step 3). |

Then this terminal is the running bot and a chat. Ctrl+C or closing the terminal stops the bot, and
`./install-writersbot.sh start` runs it in the background from then on.

Running the script again is safe: what is done already is skipped. If setup was cut off, `./install-writersbot.sh setup`
goes on from there. If an app did not install, `./install-writersbot.sh update` adds it again. If an earlier try went wrong,
`docs/FOR_THE_ORIN.md` section 8 has the commands to clean up.

Everyday commands, from the same folder: `./install-writersbot.sh start`, `stop`, `pair` (a sign-in code for a browser
or the window), `chat`, `setup`, and `update` (brings a running bot's Core, window and own apps up to this copy, each app with
its Yes card).

**2. Still on the Orin, once:** in Maps' Picture maker box, type this Orin's own address with port 8188 (the script
prints it), press Test, and say Yes on the Bridge card. That is what paints the pictures.

**3. On the writer's Windows computer:** download this repo's ZIP from GitHub, extract it, and double-click **Get the
WritersBot window**. It asks for the Orin's address, gets what it needs, and leaves a **WritersBot** shortcut on the
desktop. The window then asks for a sign-in code, from `./install-writersbot.sh pair` on the Orin.
`docs/FOR_YOUR_WINDOWS_COMPUTER.md`, Part 1 has this step by step.

Then open the window from the shortcut and say "start novel mode". Editing is in the Editor's Desk app, beside Manuscript and Maps.

**Getting the newest version**, if you already have this folder:

```bash
cd ~/WritersBotFounders && git fetch origin && git reset --hard origin/main
```

This replaces only this copy of the instructions and code. It does not touch the bot or the books, which live in the
data folder and the container. (A plain `git pull` can refuse here, because the first version's scripts needed
`chmod +x`.) A bot already set up then takes what is new with `./install-writersbot.sh update`, and the window needs F5 once after it.

If something does not go as written, `docs/FOR_THE_ORIN.md` has the long form and `docs/FOR_HER_CLAUDE.md` is for the AI
helping you. Keep the exact words of anything that fails.
