# Start here

Three steps. The first two are for whoever sits at the Orin; the third is for the writer's Windows computer.

**1. On the Orin** (it has Docker and Ollama; if Ollama is new, `ollama pull qwen3:30b-a3b` first):

```bash
git clone https://github.com/CypherSisters/WritersBotFounders.git ~/WritersBotFounders
cd ~/WritersBotFounders
./install-writersbot.sh --pictures
```

It builds the container, installs the bot from the bundle, sets up the picture program on this Orin, then runs setup.
Setup asks about the model, the vault and its PIN, the Bridge, and Toolsmith's model. Write the recovery code down.
At the end it prints the home-network address, like `http://10.0.0.25:8423`. Keep that.

Everyday commands, from the same folder: `./install-writersbot.sh start`, `stop`, `pair` (a sign-in code for a browser
or the window), `chat`.

**2. Still on the Orin, once:** in Maps' Picture maker box, type this Orin's own address with port 8188 (the script
prints it), press Test, and say Yes on the Bridge card. That is what paints the pictures.

**3. On the writer's Windows computer:** `docs/FOR_YOUR_WINDOWS_COMPUTER.md`, Part 1. It is Python 3.11, one command,
and the pair code from step 1. It leaves a **WritersBot** shortcut on the desktop.

Then open the window from the shortcut and say "start novel mode".

If something does not go as written, `docs/FOR_THE_ORIN.md` has the long form and `docs/FOR_HER_CLAUDE.md` is for the AI
helping you. Keep the exact words of anything that fails.
