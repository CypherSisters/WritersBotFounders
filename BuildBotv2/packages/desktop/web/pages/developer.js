/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/developer/page.tsx, developer/vault/page.tsx,
 * developer/brain/page.tsx, developer/bridge/page.tsx, developer/_components/AddYourOwn.tsx, CliTile.tsx,
 * CommandLine.tsx, UseCliPanel.tsx, parts.tsx and app/_components/AddATool.tsx (adapted, lane I18f: the same sections,
 * order and words as v1, in v2's plain Desktop, on v2's Core routes).
 *
 * #developer: Add your own (a folder, GitHub, Toolsmith's cloud model), the three zone cards, Add a tool, the command
 *   line, the audit log.
 * #developer/vault (Zone 1): the vault now, lock and open, how it is locked, the data folder and linked folders.
 * #developer/brain (Zone 2): the brain now, this computer, the models for each job (lane I17).
 * #developer/bridge (Zone 3): the Bridge and Toolsmith's search, what went out, Toolsmith's model, cost cap and key,
 *   the hosts Toolsmith may reach and Drawbridge's checks (read only), your own gear.
 * #developer/terminal: the command line alone (its own window).
 * #developer/github: the GitHub block alone (github.js, lane I18g; the same block is on Overview and Apps > Add),
 *   reachable from Zone 3 Bridge.
 * #developer/toolsmith: Toolsmith's cloud model alone (D101; the same tile is on Overview and Zone 3 Bridge).
 *
 * Every change that lets more leave the computer, or lets Toolsmith do more, goes through the same Yes the wizard
 * uses: a Yes card (Toolsmith's search, a model download, a folder of code), or her Yes to the company's sentence
 * (Toolsmith's cloud model, a higher cost cap). Closing, turning off and lowering apply at once. No key is shown.
 *
 * The terminal uses lane I8c's Developer shell route: POST /v1/dev/shell {shell} -> {id, cwd};
 * POST /v1/dev/shell/<id>/input {text} or {signal: "ctrl_c"}; GET /v1/dev/shell/<id>/events (SSE {t, stream,
 * text}, then {exit}); DELETE /v1/dev/shell/<id>; GET /v1/dev/shell. Nothing typed there is checked by the Bridge. */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, D = window.PuffDev, el = P.el, clear = P.clear;
  const term = { id: null, cwd: "", log: null, es: null, paint: null };
  const CONSENT = "Nothing leaves your computer without your consent.";

  /* ---- small shared parts (v1 parts.tsx, UseCliPanel.tsx) ---- */
  function back() {
    return el("a", { class: "small muted", href: "#developer", "data-dev": "back" }, [P.icon("arrow_back"), "Developer"]);
  }
  /* "puffpai" is on no PATH (D92(e)), so a command here needs this install's own program: said once, from the Core. */
  function cliWhere() {
    const line = el("p", { class: "small muted", "data-dev": "cli-where", text: D.CLI_WHERE });
    P.api("GET", "/v1/signin").then((r) => { if (r.status === 200 && r.data && r.data.cli) line.textContent = D.CLI_WHERE + " Here that is: " + r.data.cli; }).catch(() => {});
    return line;
  }
  function useCli(page) {
    const cmds = D.cliCommands(page);
    return el("details", { class: "small", "data-dev": "cli-" + page }, [el("summary", { text: "Use CLI" }),
      el("p", { class: "small muted", text: D.CLI_NOTE }), cliWhere()].concat(cmds.map((c) => {
        const copy = el("button", { class: "link", text: "Copy" });
        copy.addEventListener("click", async () => { try { await navigator.clipboard.writeText(c.command); copy.textContent = "Copied"; } catch (e) { copy.textContent = "Select it and copy"; } });
        return el("div", { class: "row" }, [el("span", { class: "grow small", text: c.what + (c.fill ? " (put yours in place of the <word>)" : "") }),
          el("code", { text: c.command }), copy]);
      })));
  }
  function header(title, lede, page) {
    return el("div", { class: "dev-head", "data-dev": "head" }, [title ? el("h2", { text: title }) : null, el("p", { class: "muted", text: lede }), useCli(page)]);
  }
  function goTo(href, title, text) {
    return el("a", { href: href, "data-goto": href, title: text }, [title]);
  }
  const say = (node, r, ok) => { node.textContent = ok || P.said(r); };

  /* ---- the command line (v1 CliTile + CommandLine) ---- */
  function terminalTile(alone) {
    const t = P.tile({ id: "terminal", icon: "terminal", title: "Command line", wide: true });
    t.body.appendChild(el("p", { class: "warn-t", text: L.SHELL_NOTE }));
    const screen = el("div", { class: "term", "aria-live": "polite" });
    const input = el("input", { placeholder: "Type a command and press Enter", "aria-label": "Command", autocomplete: "off", spellcheck: "false" });
    const shell = el("select", { "aria-label": "Shell" }, [el("option", { value: "powershell", text: "PowerShell" }), el("option", { value: "cmd", text: "Command Prompt" })]);
    /* D132: this computer's own shells, which the page can't guess: it may be open on another device. */
    P.api("GET", "/v1/signin").then((r) => {
      const names = r.status === 200 && r.data && r.data.shells;
      if (Array.isArray(names) && names[0] === "bash") { clear(shell); names.forEach((n) => shell.appendChild(el("option", { value: n, text: n }))); }
    });
    const state = el("span", { class: "small muted" });
    const startBtn = el("button", { class: "primary" }, [P.icon("play_arrow"), "New terminal"]);
    const ctrlc = el("button", {}, [P.icon("cancel"), "Ctrl+C"]);
    const close = el("button", { class: "danger" }, [P.icon("close"), "Close"]);
    const form = el("form", { class: "term-form", autocomplete: "off" }, [input, el("button", { type: "submit" }, ["Run"])]);
    const bigger = el("button", { class: "link" }, [P.icon("open_in_full"), "Bigger"]);
    const own = el("button", { class: "link" }, [P.icon("open_in_new"), "Own window"]);
    bigger.addEventListener("click", () => { const big = screen.classList.toggle("big"); screen.style.minHeight = big ? "60vh" : ""; bigger.lastChild.textContent = big ? "Smaller" : "Bigger"; });
    own.addEventListener("click", () => window.open("/#developer/terminal", "puffpai-command-line", "popup,width=900,height=620"));

    function paint() {
      clear(screen);
      (term.log ? term.log.lines : []).forEach((l) => screen.appendChild(el("span", { class: l.stream, text: l.text })));
      if (term.log && term.log.exit !== null) screen.appendChild(el("span", { class: "stderr", text: "\n[it ended, exit " + term.log.exit + "]\n" }));
      screen.scrollTop = screen.scrollHeight;
      const st = L.shellState({ id: term.id, cwd: term.cwd, shell: term.shell, busy: term.busy, closed: term.closed,
                                exited: !!(term.log && term.log.exit !== null) });
      state.textContent = st.text;
      input.disabled = !st.input;
      ctrlc.disabled = close.disabled = !st.buttons;
    }
    term.paint = paint;                       // the open stream paints whichever tile is on screen now
    function follow() {
      if (term.es) term.es.close();
      term.es = P.stream("/v1/dev/shell/" + encodeURIComponent(term.id) + "/events");   // with her login's headers (D79)
      const es = term.es;
      const handle = (ev) => {
        let data = null;
        try { data = JSON.parse(ev.data); } catch (e) { return; }
        term.log.add(data);
        if (term.log.exit !== null) es.close();
        if (term.paint) term.paint();
      };
      // Lane I8c's events are unnamed ({t, stream, text}, then {exit}). The stream ends after its timeout
      // (25 s); the stream comes back by itself with Last-Event-ID, so nothing is lost or shown twice.
      es.addEventListener("message", handle);
      es.onerror = () => { if (es.readyState === 2 && term.log && term.log.exit === null) { term.closed = true; if (term.paint) term.paint(); } };
    }
    const line = (text) => { term.log.add({ stream: "stderr", text: text + "\n" }); paint(); };
    const noRoute = () => clear(t.body).append(el("p", { class: "warn-t", text: L.SHELL_NOTE }), P.later("the Developer shell route (POST /v1/dev/shell, lane I8c)",
      "Until it is in, open PowerShell yourself and use the puffpai commands there."));
    startBtn.addEventListener("click", async () => {
      const r = await P.api("POST", "/v1/dev/shell", { shell: shell.value });
      if (P.missing(r)) { noRoute(); return; }
      if (r.status !== 201 || !r.data || !r.data.id) { state.textContent = L.shellSaid(r.status, r.data); return; }
      term.id = r.data.id; term.cwd = r.data.cwd || ""; term.shell = shell.value; term.busy = false; term.closed = false;
      term.log = new L.ShellLog();
      follow(); paint(); input.focus();
    });
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!term.id || term.busy || term.closed) return;
      const text = L.shellLine(input.value);
      if (text === null) { line("One line at a time, at most " + L.SHELL_MAX_LINE + " characters."); return; }
      input.value = "";
      term.log.add({ stream: "stdin", text: "> " + text + "\n" });
      paint();
      const r = await P.api("POST", "/v1/dev/shell/" + encodeURIComponent(term.id) + "/input", { text });
      if (r.status === 404) { term.closed = true; if (term.es) term.es.close(); line(L.SHELL_CLOSED); return; }
      if (r.status !== 200) line(L.shellSaid(r.status, r.data));
    });
    ctrlc.addEventListener("click", async () => {
      term.busy = true; paint();                   // no line goes in while Ctrl+C is on its way
      const r = await P.api("POST", "/v1/dev/shell/" + encodeURIComponent(term.id) + "/input", { signal: "ctrl_c" });
      term.busy = false;
      if (r.status === 404) { term.closed = true; if (term.es) term.es.close(); line(L.SHELL_CLOSED); return; }
      line(r.status === 200 ? L.ctrlcSaid(r.data && r.data.result) : L.shellSaid(r.status, r.data));
      if (!input.disabled) input.focus();
    });
    close.addEventListener("click", async () => {
      await P.api("DELETE", "/v1/dev/shell/" + encodeURIComponent(term.id));
      if (term.es) term.es.close();
      term.id = null; term.closed = false; term.busy = false; paint();
    });
    t.body.append(el("div", { class: "row" }, [shell, startBtn, ctrlc, close, state, el("span", { class: "grow" }), bigger, alone || P.oneWindow ? null : own]), screen, form);
    paint();
    // Is the route here, and is a terminal still open from before?
    P.api("GET", "/v1/dev/shell").then((r) => {
      if (P.missing(r)) { noRoute(); return; }
      if (r.status !== 200) { state.textContent = L.shellSaid(r.status, r.data); return; }
      // A bare list [{id, shell, started, idle_s, mine}]; only this device's own can be followed.
      const mine = (Array.isArray(r.data) ? r.data : []).filter((s) => s && s.mine && s.id);
      if (!term.id && mine.length) { term.id = mine[0].id; term.cwd = ""; term.shell = mine[0].shell || ""; term.busy = false; term.closed = false; term.log = new L.ShellLog(); follow(); paint(); }
    });
    return t.node;
  }

  /* ---- the overview (v1 page.tsx) ---- */
  function folderCard() {                      // v1 AddYourOwn "A folder of my code", on v2's package add (with link)
    const t = P.tile({ id: "own-folder", icon: "folder_code", title: "A folder of my code" });
    const path = el("input", { class: "grow", type: "text", placeholder: "C:\\Users\\me\\code\\my-app", autocomplete: "off", spellcheck: "false", "aria-label": "Your folder" });
    const said = el("p", { class: "small" });
    const cardBox = el("div");
    const add = el("button", { class: "primary", text: "Add this folder" });
    add.addEventListener("click", async () => {
      const f = D.folderPath(path.value);
      clear(cardBox);
      if (!f.ok) { said.textContent = f.line; return; }
      add.disabled = true; said.textContent = "Reading it";
      const r = await P.api("POST", "/v1/packages", { source: f.path, link: true });
      add.disabled = false;
      if (r.status !== 202 || !r.data || !r.data.consent_id) { said.textContent = P.said(r, "That was not accepted. Nothing was added."); return; }
      said.textContent = "Its Yes card is below, and on Your turn.";
      P.consentInline(cardBox, r.data.consent_id, async (answer) => {
        if (answer !== "yes") { said.textContent = "Nothing was added."; return; }
        said.textContent = "Adding it";
        let done = L.installState(null);
        for (let i = 0; r.data.id && i < 600 && !done.final; i++) {
          done = L.installState((await P.loadRegistry()).find((x) => x.id === r.data.id));
          if (!done.final) await P.sleep(1000);
        }
        said.textContent = done.final ? done.line : "It is still being added. Apps shows it when it is ready.";
      });
    });
    t.body.append(el("p", { class: "small muted", text: "Your bot checks it and shows its Yes card first. It runs right there in your folder, never copied or deleted." }),
      el("div", { class: "row" }, [path, add]), said, cardBox,
      el("p", { class: "small muted", text: "Everything is a package: one folder with one puffpai.json. Copy packages\\example-page to start one, check it with puffpai check <folder>, add it with puffpai add <folder> --link. The guide is docs/04_DEVELOPERS.md in the BuildBot folder." }),
      el("div", { class: "row" }, [el("a", { href: "#apps/available", text: "The PuffPAI store" }), el("a", { href: "#apps/add", text: "Add on Apps" })]));
    return t.node;
  }

  async function auditTile() {
    const t = P.tile({ id: "audit", icon: "receipt_long", title: "Audit log", wide: true });
    const r = await P.api("GET", "/v1/audit?limit=60");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    t.head.appendChild(el("span", { class: "stat", text: r.data.chain_ok ? "CHAIN OK" : "CHAIN DAMAGED AT LINE " + r.data.bad_line }));
    const lines = (r.data.lines || []).map(L.auditLine).reverse().slice(0, 40);
    t.body.appendChild(lines.length ? el("details", {}, [el("summary", { class: "small", text: "The last " + lines.length + (lines.length === 1 ? " line" : " lines") }),
      el("ul", { class: "lines" }, lines.map((l) => el("li", {}, [el("span", { class: "when", text: l.when.replace("T", " ").slice(0, 19) }), el("span", { text: l.text })])))])
                                    : el("p", { class: "empty", text: "No lines yet." }));
    return t.node;
  }

  /* The parts of a zone a package may fill (moved here from Settings): for developers. Changing one shows a Yes card. */
  async function partsTile() {
    const t = P.tile({ id: "parts", icon: "tune", title: "Parts a package can replace", wide: true });
    const r = await P.api("GET", "/v1/providers");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const rows = L.slotRows(r.data);
    t.body.appendChild(el("details", {}, [el("summary", { class: "small", text: rows.length + (rows.length === 1 ? " part" : " parts") + ". Changing one shows a Yes card first." }),
      el("ul", { class: "lines" }, rows.map((s) => {
        const said = el("div", { class: "small" });
        let ctl = null;
        if (s.canPick) {
          const sel = el("select", { "aria-label": s.slot }, s.installed.map((id) => el("option", { value: id, text: P.names()[id] || id })));
          if (s.active) sel.value = s.active;
          ctl = el("span", { class: "row" }, [sel, el("button", { on: { click: async () => {
            const x = await P.api("PUT", "/v1/providers/" + encodeURIComponent(s.slot), { id: sel.value });
            clear(said);
            if (x.status !== 202) { said.textContent = P.said(x); return; }
            const box = el("div"); said.appendChild(box); P.consentInline(box, x.data.consent_id);
          } } }, ["Use"])]);
        }
        const active = s.one ? (s.active ? P.names()[s.active] || s.active : "the Core's own") : (s.active.length ? s.active.join(", ") : "none added");
        return el("li", {}, [el("div", { class: "grow" }, [el("code", { text: s.slot }), el("div", { class: "small muted", text: (s.one ? "In use: " : "Added: ") + active }), said]), ctl]);
      }))]));
    return t.node;
  }

  async function overview(sec) {
    const [vault, model, bridge, smith] = await Promise.all([P.api("GET", "/v1/vault/status"), P.api("GET", "/v1/model"), P.api("GET", "/v1/bridge"), toolsmithHere()]);
    const ok = (r) => (r.status === 200 ? r.data : null);
    const head = header("", "Add what you already have, and see the three parts of your bot.", "overview");
    const own = el("div", { class: "tiles", "data-dev": "add-your-own" }, [folderCard(), window.PuffGitHub ? window.PuffGitHub.block({ where: "developer" }) : null,
      window.PuffSearchKeys ? window.PuffSearchKeys.block() : null, window.PuffAddApi ? window.PuffAddApi.block() : null,
      window.PuffOwnPrograms ? window.PuffOwnPrograms.block() : null, smith]);
    const cards = el("div", { class: "tiles", "data-dev": "zones" }, D.zoneCards({ vault: ok(vault), model: ok(model), bridge: ok(bridge) }).map((c) => {
      const t = P.tile({ id: "zone-" + c.id, icon: c.icon, title: c.name, stat: c.zone.toUpperCase() });
      t.body.append(el("p", { class: "now", "data-zone-status": c.id, text: c.status }), el("p", { class: "small muted", text: c.more }),
        el("div", { class: "row" }, [el("a", { href: c.href, "data-zone-open": c.id }, ["Open ", P.icon("arrow_forward")])]));
      return t.node;
    }));
    const tools = el("div", { class: "tiles" }, [terminalTile(false)]);
    clear(sec).append(head, cards, el("h2", { class: "micro", text: "Add your own" }), own, el("h2", { class: "micro", text: "Tools" }), tools);
    tools.append(await auditTile(), await partsTile());
  }

  /* ---- Zone 1: Vault (v1 developer/vault/page.tsx) ---- */
  async function vaultNow(redraw) {
    const t = P.tile({ id: "vault-now", icon: "lock", title: "Right now", wide: true });
    const r = await P.api("GET", "/v1/vault/status");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: "Your bot did not answer, so the vault cannot be shown right now. " + P.said(r) })); return t.node; }
    const v = L.vaultView(r.data), place = D.vaultPlace(r.data), act = D.vaultActions(r.data);
    const waiting = v.inbox === 0 ? "Nothing is waiting to go into the vault." : v.inbox + (v.inbox === 1 ? " thing waits" : " things wait") + " to go into the vault the next time you open it.";
    t.body.append(P.kv([["Vault", D.vaultStatus(r.data)], ["Kept in", place.where], ["That folder", place.line], ["Keys kept", v.items],
      ["Ways in", v.waysIn.join(", ")], ["Idle lock", v.idle], ["Waiting", r.data.state === "locked" ? waiting : ""], ["Why", v.why]]));
    const said = el("p", { class: "small", "data-said": "vault" });
    const row = el("div", { class: "row" });
    if (act.lock) row.appendChild(el("button", { class: "primary", on: { click: async () => {
      const x = await P.api("POST", "/v1/vault/lock", {});
      if (x.status !== 200) { say(said, x); return; }
      P.drawPrivate(); redraw();
    } } }, [P.icon("lock"), "Lock it now"]));
    if (act.unlock) {
      const pin = P.pin("Your PIN");
      const open = el("button", { class: "primary", on: { click: async () => {
        const secret = pin.value; pin.value = "";
        if (!secret) { said.textContent = "Type your PIN first."; return; }
        const x = await P.api("POST", "/v1/vault/unlock", { method: "pin", secret });
        if (x.status !== 200) { say(said, x, P.said(x, "It did not open.")); return; }
        P.drawPrivate(); redraw();
      } } }, [P.icon("lock_open"), "Open it"]);
      row.append(pin, open);
    }
    if (act.setup) row.appendChild(el("button", { on: { click: () => P.go("setup", "vault") } }, [P.icon("rocket_launch"), "Make it in Setup"]));
    if (act.wait) said.textContent = "Too many tries. Wait " + act.wait + " seconds, then try again.";
    t.body.append(row, said, el("p", { class: "faint small", text: "Nothing secret is shown here: no PIN, no recovery code, no key." }));
    return t.node;
  }

  function howLocked() {
    const t = P.tile({ id: "vault-how", icon: "shield_lock", title: "How it is locked", wide: true });
    t.body.append(el("p", { class: "small", text: "Your PIN goes through Argon2id, a slow scrambler that needs 64 MB of memory for every try, before it can open the lock, so guessing is slow. Your PIN and your recovery code are never written down anywhere on this computer." }),
      el("p", { class: "small", text: "Password managers and programs that lock a whole drive (BitLocker, VeraCrypt, KeePass, Bitwarden, 1Password) cannot be used as the vault itself. Your vault can live inside a drive you already lock: unlock that drive yourself first." }));
    return t.node;
  }

  async function foldersTile(redraw) {
    const t = P.tile({ id: "folders", icon: "folder_open", title: "Your folders", wide: true });
    const r = await P.api("GET", "/v1/folders");
    if (P.missing(r)) { t.body.appendChild(P.later("the folders route (GET /v1/folders, lane I18f)")); return t.node; }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const f = D.foldersView(r.data);
    const said = el("p", { class: "small", "data-said": "folders" });
    t.body.append(el("p", { class: "small muted", text: f.line }), P.kv([["Data folder", f.data], ["That folder", f.dataLine]]));
    if (f.setup) {
      const use = el("button", { class: "primary", on: { click: async () => {
        const x = await P.api("POST", "/v1/folders/from-setup", {});
        if (x.status !== 200) { say(said, x); return; }
        redraw();
      } } }, ["Use them"]);
      t.body.appendChild(el("div", { class: "card note", "data-dev": "from-setup" }, [el("p", { text: f.setup.line }),
        f.setup.folders.length ? el("ul", { class: "small" }, f.setup.folders.map((x) => el("li", { text: x }))) : null, el("div", { class: "row" }, [use])]));
    }
    t.body.appendChild(el("p", { class: "micro", text: "Linked folders" }));
    if (f.none) t.body.appendChild(el("p", { class: "empty", text: f.none }));
    else t.body.appendChild(el("ul", { class: "lines", "data-dev": "linked" }, f.linked.map((x) => {
      const un = el("button", { class: "link", text: "Unlink", on: { click: async () => {
        const y = await P.api("POST", "/v1/folders/unlink", { path: x.path });
        if (y.status !== 200) { say(said, y); return; }
        redraw();
      } } });
      return el("li", { "data-linked": x.path }, [el("code", { class: "grow", text: x.path }), el("span", { class: "small muted", text: x.line }), un]);
    })));
    const path = el("input", { class: "grow", type: "text", placeholder: "C:\\Users\\me\\Documents\\Recipes", autocomplete: "off", spellcheck: "false", "aria-label": "A folder to link" });
    const link = el("button", { on: { click: async () => {
      const p = D.folderPath(path.value);
      if (!p.ok) { said.textContent = p.line; return; }
      const y = await P.api("POST", "/v1/folders/link", { path: p.path });
      if (y.status !== 200) { say(said, y); return; }
      redraw();
    } } }, [P.icon("add_link"), "Link this folder"]);
    t.body.append(el("div", { class: "row" }, [path, link]), said);
    return t.node;
  }

  async function vaultPage(sec) {
    const redraw = () => vaultPage(sec);
    const tiles = await Promise.all([vaultNow(redraw), foldersTile(redraw)]);
    clear(sec).append(back(), header("Zone 1: Vault", "Where your private things are kept, how they are locked, and the folders your bot reads.", "vault"),
      el("div", { class: "row lede", "data-dev": "goto" }, [goTo("#vault", "Vault page", "Lock or open the vault, make a new recovery code, change your PIN, or open it if you forgot your PIN.")]),
      el("div", { class: "tiles" }, [tiles[0], tiles[1], P.knowledgePlaceTile(), howLocked()]));
  }

  /* ---- Zone 2: Brain (v1 developer/brain/page.tsx) ---- */
  async function brainNow() {
    const t = P.tile({ id: "brain-now", icon: "neurology", title: "Right now", wide: true });
    const [r, menu] = await Promise.all([P.api("GET", "/v1/model"), P.api("GET", "/v1/model/menu")]);
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: "Your bot did not answer, so the Brain cannot be shown right now. " + P.said(r) })); return t.node; }
    const s = r.data.settings || {}, eff = s.effective || {};
    const b = L.brainView(r.data);
    t.body.append(P.kv([["Model in use", s.model || "None chosen yet"], ["Answering", b.ok ? "Yes" : b.words + (b.reason ? ": " + b.reason : "")],
      ["Runs at", s.address], ["Runs on", s.runtime], ["Context", eff.context], ["Tool budget", eff.tool_budget], ["Pinned tools", (s.pinned || []).join(", ")],
      ["This computer", menu.status === 200 ? D.computerLine(menu.data.census) : ""]]),
      el("p", { class: "small muted", text: "Your bot uses Ollama on this computer. Its address is a setup question: pointing your bot at another computer lets more out, so it is never changed from here." }));
    return t.node;
  }

  /* Lane I17: the Models section. GET /v1/models: what is on this computer and what each model is used for;
   * per role, the catalog rows that fit (strength, RAM, size). Add: POST /v1/models/pull {model}, then its own
   * Yes card here, then the download's progress from GET /v1/models. Use for: PUT /v1/models/roles {role: model};
   * for chat, v1's read-back first ("Your bot will think with ..."), then Yes, use it. */
  function modelsTile(after) {
    const t = P.tile({ id: "models", icon: "memory", title: "Models", wide: true });
    const box = el("div", { "data-models": "" });
    t.body.appendChild(box);
    const wait = (ms) => new Promise((ok) => setTimeout(ok, ms));
    async function follow(model, note) {
      for (let i = 0; i < 7200; i++) {              // the Core keeps going if this page goes
        const r = await P.api("GET", "/v1/models");
        const p = r.status === 200 && r.data && r.data.pulls ? r.data.pulls[model] || {} : {};
        if (p.line) note.textContent = p.line + (typeof p.done_gb === "number" && p.size_gb ? " (" + p.done_gb + " of " + p.size_gb + " GB)" : "");
        if (p.state && p.state !== "asking" && p.state !== "downloading") break;
        await wait(1500);
      }
      draw();
    }
    async function use(role, id, note) {
      const got = await P.api("PUT", "/v1/models/roles", { [role]: id });
      if (got.status !== 200) { note.textContent = P.said(got); return; }
      draw();
      if (role === "chat" && after) after();
    }
    async function draw() {
      const r = await P.api("GET", "/v1/models");
      clear(box);
      if (P.missing(r)) { box.appendChild(P.later("the models route (GET /v1/models, lane I17)")); return; }
      if (r.status !== 200) { box.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      const v = L.modelsView(r.data);
      if (v.problem) box.appendChild(el("p", { class: "warn-t", text: v.problem }));
      box.appendChild(el("p", { class: "micro", text: "On this computer" }));
      box.appendChild(v.installed.length ? el("ul", { class: "lines", "data-models": "installed" }, v.installed.map((m) =>
        el("li", { "data-model": m.name }, [el("b", { text: m.name }), el("span", { text: " " + (m.size ? m.size + ". " : "") + "Used for: " + m.usedFor })])))
        : el("p", { class: "empty", text: "No models on this computer yet." }));
      v.sections.forEach((sec) => {
        const part = el("div", { "data-role": sec.role }, [el("p", { class: "micro", text: sec.title }), el("p", { class: "small muted", text: sec.now })]);
        if (sec.none) part.appendChild(el("p", { class: "small", text: sec.none }));
        sec.rows.forEach((row) => {
          const note = el("p", { class: "small", text: row.said });
          const cardBox = el("div");
          const btns = el("div", { class: "row" });
          if (row.canAdd) {
            const add = el("button", { class: "primary", text: "Add" });
            add.addEventListener("click", async () => {
              add.disabled = true;
              const got = await P.api("POST", "/v1/models/pull", { model: row.id });
              if (got.status !== 202 || !got.data || !got.data.consent_id) { note.textContent = P.said(got); add.disabled = false; return; }
              note.textContent = "Its Yes card is below, and on Your turn.";
              P.consentInline(cardBox, got.data.consent_id, () => follow(row.id, note));
            });
            btns.appendChild(add);
          }
          if (row.canUse) {
            const btn = el("button", { text: "Use for " + sec.title.toLowerCase() });
            btn.addEventListener("click", () => {
              if (sec.role !== "chat") { use(sec.role, row.id, note); return; }
              clear(cardBox).appendChild(el("div", { class: "card", "data-dev": "use-chat" }, [
                el("p", { text: "Your bot will think with " + row.label + ". Its address stays the same." }),
                el("div", { class: "row" }, [el("button", { class: "primary", text: "Yes, use it", on: { click: () => use("chat", row.id, note) } }),
                  el("button", { class: "link", text: "Not now", on: { click: () => clear(cardBox) } })])]));
            });
            btns.appendChild(btn);
          }
          if (row.using) btns.appendChild(el("span", { class: "stat", text: "IN USE" }));
          part.appendChild(el("div", { class: "model-row", "data-model": row.id }, [el("b", { text: row.label }),
            el("p", { class: "small", text: row.strength }), el("p", { class: "small muted", text: row.facts }), note, btns, cardBox]));
        });
        box.appendChild(part);
      });
      if (v.bridge) box.appendChild(el("p", { class: "faint small", text: v.bridge }));
    }
    draw();
    return t.node;
  }

  async function brainPage(sec) {
    const now = await brainNow();
    const fresh = async () => { const n = await brainNow(); const old = sec.querySelector("[data-tile=brain-now]"); if (old) old.replaceWith(n); };
    clear(sec).append(back(), header("Zone 2: Brain", "The model your bot thinks with, where it runs, and the models for each job.", "brain"),
      el("div", { class: "tiles" }, [now, modelsTile(fresh), window.PuffToolServers ? window.PuffToolServers.block() : null]));
  }

  /* ---- Zone 3: Bridge (v1 developer/bridge/page.tsx) ---- */
  async function bridgeNow(redraw) {
    const t = P.tile({ id: "bridge-now", icon: "public", title: "Right now", wide: true });
    const r = await P.api("GET", "/v1/bridge");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: "Your bot did not answer, so the Bridge cannot be shown right now. " + P.said(r) })); return t.node; }
    const b = r.data;
    const sm = b.safe_mode || {};
    t.body.append(P.kv([["Bridge", b.state === "open" ? "Open" : "Closed"], ["Its setting", b.setting === "open" ? "Open: only what you said Yes to goes out" : "Closed"],
      ["Toolsmith's search", b.search ? "On, while it builds" : (b.switches || {})["toolsmith:search"] ? "On, once the Bridge is open" : "Off"],
      ["Safe mode", sm.on ? (sm.reasons || []).join(" ") : ""]]),
      el("p", { "data-consent-line": "", text: L.bridgeView(b).text }));
    const said = el("p", { class: "small", "data-said": "bridge" });
    const cardBox = el("div");
    const row = el("div", { class: "row" });
    const btn = await P.bridgeButton(b, () => { P.drawChips(); P.drawPrivate(); redraw(); });
    if (btn) row.appendChild(btn);
    const switchOn = !!(b.switches || {})["toolsmith:search"];
    if (switchOn) row.appendChild(el("button", { on: { click: async () => {
      const x = await P.api("POST", "/v1/bridge/search", { on: false });
      if (x.status !== 200) { say(said, x); return; }
      P.drawChips(); redraw();
    } } }, [P.icon("search_off"), "Turn off Toolsmith's search"]));
    else row.appendChild(el("button", { on: { click: async () => {
      const x = await P.api("POST", "/v1/bridge/search", { on: true });
      if (P.missing(x)) { said.textContent = "This Core has no route for Toolsmith's search yet. Setup's Bridge step changes it."; return; }
      if (x.status !== 202 || !x.data || !x.data.consent_id) { say(said, x); return; }
      said.textContent = "Its Yes card is below, and on Your turn.";
      P.consentInline(cardBox, x.data.consent_id, (answer) => {
        if (answer !== "yes") { said.textContent = "Toolsmith's search stays off."; return; }
        redraw(b.setting === "open" ? "Your Yes is kept. Toolsmith may search while it builds."
          : "Your Yes is kept. Toolsmith's search starts when the Bridge is open: press Open the Bridge.");
      });
    } } }, [P.icon("travel_explore"), "Let Toolsmith search while it builds"]));
    t.body.append(row, said, cardBox, el("p", { class: "faint small", text: "Closing the Bridge or turning the search off happens at once. Turning the search on asks your Yes first." }));
    t.said = said;
    return t;
  }

  async function ledgerTile() {
    const t = P.tile({ id: "ledger", icon: "receipt", title: "What went out", wide: true });
    const led = await P.api("GET", "/v1/bridge/ledger?limit=20");
    if (led.status !== 200 || !Array.isArray(led.data)) { t.body.appendChild(el("p", { class: "fail", text: P.said(led) })); return t.node; }
    const names = P.names();
    const lines = led.data.slice().reverse().map((x) => L.ledgerLine(x, names)).slice(0, 5);
    t.body.appendChild(lines.length ? el("ul", { class: "lines" }, lines.map((l) => el("li", {}, [el("span", { class: "when", text: String(l.when).replace("T", " ").slice(0, 19) }), el("span", { text: l.text })])))
                                    : el("p", { class: "empty", text: "Nothing yet." }));
    if (lines.length) t.body.appendChild(el("div", { class: "row end" }, [el("a", { href: "#outside", text: "All of it" })]));
    return t.node;
  }

  /* Toolsmith's cloud model: the company and model in use, its cost cap and key (v2 Settings' tile, plus its key and
   * a lower cap at once). The same tile on Overview, Zone 3 Bridge and #developer/toolsmith (D101): it uses no page
   * ids, and asks *redraw* to draw it again after a change of model, so each page says how. The cloud choice is the
   * wizard's own Yes: she ticks the company's sentence, and the Core checks its sha (PUT /v1/toolsmith/model).
   *
   * THE KEY, as in github.js. It is read from the password box once, sent once to the Core, and the box is cleared
   * the moment it is read. It is never written to storage, the address bar, the console or the screen, and no Core
   * reply carries it back. A company's key box goes when the company changes, so a key never goes to another one.
   *
   * ONE THING AT A TIME. While a key or model write is on its way, nothing here can be changed or pressed: no second
   * write starts, the company can't change under it, and no key can be pasted into a box a redraw would take away. */
  async function toolsmithTile(redraw, note) {
    const t = P.tile({ id: "toolsmith", icon: "construction", title: "Toolsmith's cloud model", wide: true });
    const [st, prov, keys, vault] = await Promise.all([P.api("GET", "/v1/toolsmith"), P.api("GET", "/v1/toolsmith/providers"), P.api("GET", "/v1/toolsmith/keys"), P.api("GET", "/v1/vault/status")]);
    const stat = el("span", { class: "stat", "data-toolsmith": "stat" });
    t.head.appendChild(stat);
    if (st.status !== 200) {
      stat.textContent = "CAN'T TELL";
      t.body.append(el("p", { class: "fail", text: P.said(st) }), el("p", { class: "small", "data-said": "toolsmith", text: note || "" }));
      return t.node;
    }
    const providers = prov.status === 200 && Array.isArray(prov.data) ? prov.data : [];
    const noList = !providers.length;               // no companies to read: nothing here may be changed
    const vaultState = vault.status === 200 && vault.data ? vault.data.state : "";
    const listOf = (r) => (r.status === 200 && Array.isArray(r.data) ? r.data : []);
    let keyList = listOf(keys);
    const nowBox = el("div", { "data-toolsmith": "now" });
    function paintNow() {
      const now = D.toolsmithNow(st.data, keyList, vaultState);
      stat.textContent = now.stat;
      clear(nowBox).appendChild(P.kv([["Now", now.line], ["Cost cap", now.cap !== null ? "$" + now.cap + " per build" : ""], ["Key", now.key]]));
    }
    paintNow();
    t.body.append(nowBox, el("p", { class: "small muted", text: "Toolsmith is the only agent that may use a cloud model, and only if you pick one here. Everything else runs on this computer." }));
    const pick = el("select", { "aria-label": "Where Toolsmith thinks" }, [el("option", { value: "", text: "This computer" })]
      .concat(providers.map((p) => el("option", { value: p.id, text: p.company + (p.problems && p.problems.length ? " (not ready)" : "") }))));
    pick.value = st.data.where === "cloud" ? st.data.provider || "" : "";
    const detail = el("div");
    const said = el("p", { class: "small", "data-said": "toolsmith", text: note || "" });
    let get = null;
    let liveKey = null;                              // the key box on screen now, for Save
    let shown = pick.value;                          // the company its details on screen are for
    let busy = false;
    let choices = [];                                // the model, cap and tick on screen
    let keyParts = [];                               // the key box and its buttons on screen
    const save = el("button", { class: "primary" }, [P.icon("save"), "Save"]);
    function hold() {
      [pick, save].concat(choices, keyParts).forEach((n) => { n.disabled = busy; });
      if (noList) pick.disabled = save.disabled = true;
    }
    /* One write at a time, with its line on *line*: every way out lets go, and never says an error's own words. */
    async function oneAtATime(line, words, work) {
      if (busy) return;
      busy = true; hold();
      line.textContent = words;
      try { await work(); } catch (e) { if (line.textContent === words) line.textContent = "That did not finish. Nothing more was sent."; }
      finally {
        busy = false; hold();
        if (line.textContent === words) line.textContent = "";
      }
    }
    /* Its key, on its own: saving or taking it out draws only this part, so her tick and her choices stay. */
    function paintKey(box, p, line) {
      if (shown !== p.id || !box.parentNode) return;  // not the company on screen: liveKey stays on the box she sees
      const k = keyList.find((x) => x.provider === p.id) || {};
      const name = p.key ? p.key.vault_name : "toolsmith." + p.id;
      const keyBox = el("input", { type: "password", autocomplete: "off", spellcheck: "false", placeholder: (k.kept === true ? "New " : "Paste your ") + p.company + " key", "aria-label": p.company + " key" });
      liveKey = keyBox;
      const keySaid = el("p", { class: "small", "data-said": "toolsmith-key", text: line || "" });
      async function fresh(saidLine) {
        keyList = listOf(await P.api("GET", "/v1/toolsmith/keys"));   // can't read it: "can't tell", never the old answer
        paintNow();
        paintKey(box, p, saidLine);
      }
      const keep = el("button", { "data-toolsmith": "key-save", on: { click: () => {
        if (busy) return;
        let value = keyBox.value; keyBox.value = "";   // cleared the moment it is read
        if (!value) { keySaid.textContent = "Paste the key first."; return; }
        if (!value.trim()) { keySaid.textContent = "That key is empty."; return; }
        return oneAtATime(keySaid, "Saving...", async () => {
          const x = await P.api("PUT", "/v1/toolsmith/keys/" + encodeURIComponent(p.id), { value });
          value = "";
          if (x.status === 200 || x.status === 202) { await fresh((x.data && x.data.line) || "Saved in your vault."); return; }
          say(keySaid, x);
        });
      } } }, [P.icon("key"), k.kept === true ? "Change the key" : "Save the key"]);
      const out = k.kept === true ? el("button", { class: "link", on: { click: () => oneAtATime(keySaid, "Taking it out...", async () => {
        const x = await P.api("DELETE", "/v1/toolsmith/keys/" + encodeURIComponent(p.id));
        if (x.status === 200) { await fresh((x.data && x.data.line) || "Taken out."); return; }
        say(keySaid, x);
      }) } }, ["Take the key out"]) : null;
      keyParts = [keyBox, keep].concat(out ? [out] : []);
      const keyRow = el("div", { class: "row" }, [keyBox, keep, out,
        p.key && /^https:\/\//i.test(String(p.key.get_it || "")) ? el("button", { class: "link", on: { click: () => window.open(p.key.get_it, "_blank", "noopener,noreferrer") } }, ["Get a key"]) : null]);
      const words = k.kept === true ? "A key is kept in your vault as " + name + ". It is never shown again."
        : k.kept === false ? "No key is kept yet. It goes into your vault as " + name + ". It is never shown again."
        : vaultState === "not_set_up" ? "Set up the vault first, so its key has a safe home. It goes into your vault as " + name + "."
        : vaultState === "locked" ? "Your vault is locked, so a key saved now waits in its inbox until you open it. Open your vault to see whether a key is kept. It goes in as " + name + ". It is never shown again."
        : k.kept === null ? "Open your vault to see whether a key is kept. It goes into your vault as " + name + ". It is never shown again."
        : "Can't tell right now whether a key is kept. It goes into your vault as " + name + ". It is never shown again.";
      clear(box).append(el("p", { class: "micro", text: "Its key" }), el("p", { class: "small muted", text: words }),
        vaultState === "not_set_up" ? el("div", { class: "row" }, [el("a", { href: "#developer/vault", text: "Set up the vault" })]) : "", keyRow, keySaid);
      hold();
    }
    function paint() {
      clear(detail); get = null; liveKey = null; choices = []; keyParts = [];
      shown = pick.value;
      const p = providers.find((x) => x.id === pick.value);
      if (!p) {                                      // no company chosen: the key's place shows, closed, and says why
        detail.append(el("p", { class: "micro", text: "Its key" }));
        if (noList) { detail.append(el("p", { class: "small", "data-toolsmith": "no-companies", text: "Can't read the companies just now, so nothing can be changed here." })); return; }
        detail.append(el("p", { class: "small", "data-toolsmith": "pick-first", text: "Pick a company first. Its key box and what Toolsmith sends to it show here." }),
          el("div", { class: "row" }, [el("input", { type: "password", disabled: true, autocomplete: "off", placeholder: "Pick a company first", "aria-label": "Cloud model key" })]));
        return;
      }
      const same = st.data.where === "cloud" && st.data.provider === p.id;
      const model = el("select", { "aria-label": "Model" }, p.models.map((m) => el("option", { value: m.id, text: m.name })));
      model.value = same && p.models.some((m) => m.id === st.data.model) ? st.data.model : p.default_model || (p.models[0] || {}).id;
      const cap = el("input", { type: "number", min: "0", step: "0.5", value: String(same ? st.data.cost_cap_usd : p.cost_cap_usd), "aria-label": "Cost cap per build, dollars" });
      const agree = el("input", { type: "checkbox", "aria-label": "Yes to what Toolsmith sends" });
      choices = [model, cap, agree];
      const keyPart = el("div", { "data-toolsmith": "key" });
      detail.append(el("div", { class: "row" }, [model, el("span", { class: "small", text: "at most $" }), cap, el("span", { class: "small", text: "per build" })]),
        same && st.data.consent_changed ? el("p", { class: "small", "data-toolsmith": "consent-changed", text: st.data.consent_changed + " Toolsmith sends it nothing until you tick Yes and press Save." }) : "",
        el("p", { class: "small", "data-toolsmith": "consent", text: p.consent }),
        el("label", { class: "opt" }, [agree, el("span", { class: "small", text: "Yes to that: this is what Toolsmith sends to " + p.company + "." })]),
        keyPart);
      paintKey(keyPart, p);
      get = () => ({ provider: p.id, company: p.company, model: model.value, cap: cap.value, agree: agree.checked, sha: p.consent_sha, same: same && !st.data.consent_changed && model.value === st.data.model, keyPart: keyPart, p: p });
    }
    pick.addEventListener("change", () => {
      if (busy) { pick.value = shown; return; }      // never a new company under a write on its way
      said.textContent = ""; paint();
    });
    paint();
    hold();
    /* A key pasted before Save goes first, and only after her Yes and the cap are in order: a key that is refused
     * changes nothing, and Toolsmith is never moved to a company on a key that did not go in. */
    async function keyFirst(g, value) {
      if (!value) return { ok: true, line: "" };
      const x = await P.api("PUT", "/v1/toolsmith/keys/" + encodeURIComponent(g.provider), { value });
      if (x.status !== 200 && x.status !== 202) { say(said, x); return { ok: false, line: "" }; }
      return { ok: true, line: ((x.data && x.data.line) || "Saved in your vault.") + " " };
    }
    /* Drawn again with its Saved line. If that fails, what was saved is still said. */
    async function again(line) {
      try { await redraw(line); } catch (e) { said.textContent = line; }
    }
    save.addEventListener("click", () => {
      if (busy || noList) return;
      if (!pick.value || !get) {                   // narrowing: this computer, at once
        return oneAtATime(said, "Saving...", async () => {
          const r = await P.api("PUT", "/v1/toolsmith/model", { where: "local" });
          if (r.status !== 200) { say(said, r); return; }
          await again("Saved: Toolsmith uses this computer.");
        });
      }
      const g = get();
      const change = D.capChange(st.data.where === "cloud" ? st.data.cost_cap_usd : null, g.cap);
      if (change === "bad") { said.textContent = "The cost cap is a number of dollars."; return; }
      const atOnce = g.same && change !== "higher" && !g.agree;   // the same company and model, a lower cap: at once
      if (!atOnce && !g.agree) { said.textContent = (change === "higher" && g.same ? "A higher cap needs your Yes again. " : "") + "Tick Yes to what Toolsmith sends first."; return; }
      let value = liveKey ? liveKey.value : "";
      if (liveKey) liveKey.value = "";             // cleared the moment it is read
      if (value && !value.trim()) { said.textContent = "That key is empty."; return; }
      return oneAtATime(said, "Saving...", async () => {
        const k = await keyFirst(g, value);
        value = "";
        if (!k.ok) return;
        const r = atOnce ? await P.api("PUT", "/v1/toolsmith/cap", { cost_cap_usd: Number(g.cap) })
          : await P.api("PUT", "/v1/toolsmith/model", { where: "cloud", provider: g.provider, model: g.model, cost_cap_usd: Number(g.cap), consent_sha: g.sha });
        if (r.status !== 200) {
          if (k.line) { keyList = listOf(await P.api("GET", "/v1/toolsmith/keys")); paintNow(); paintKey(g.keyPart, g.p, k.line.trim()); }
          say(said, r); return;
        }
        await again(k.line + (atOnce ? "Saved: at most $" + r.data.cost_cap_usd + " per build." : "Saved: " + g.company + ", " + r.data.model + ", at most $" + r.data.cost_cap_usd + " per build."));
      });
    });
    t.body.append(el("div", { class: "row" }, [pick, save]), detail, said,
      el("p", { class: "faint small", text: "This computer, or a lower cap, is used at once. A company, another model or a higher cap asks your Yes to what Toolsmith sends. A key pasted above goes in with Save too." }));
    return t.node;
  }

  /* The tile drawn again in its own place (Overview, #developer/toolsmith): the page around it is left alone, so a
   * running command line keeps its screen. Its "Saved" line goes in its own data-said slot. */
  async function toolsmithHere(note) {
    const node = await toolsmithTile(async (line) => {
      const next = await toolsmithHere(line);
      if (node.parentNode) node.parentNode.replaceChild(next, node);
    }, note);
    return node;
  }

  async function hostsTile() {
    const t = P.tile({ id: "toolsmith-hosts", icon: "dns", title: "Where Toolsmith may reach" });
    const r = await P.api("GET", "/v1/toolsmith/hosts");
    if (P.missing(r)) { t.body.appendChild(P.later("the Toolsmith hosts route (GET /v1/toolsmith/hosts, lane I18f)")); return t.node; }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    t.body.append(el("ul", { class: "lines", "data-dev": "hosts" }, D.hostLines(r.data).map((h) => el("li", {}, [el("div", { class: "grow" }, [el("b", { text: h.what }),
      h.hosts ? el("div", { class: "small muted", text: h.hosts }) : null]), el("span", { class: "small", text: h.state })]))),
      el("p", { class: "small muted", text: r.data.line || "" }), el("p", { class: "faint small", text: "This list is part of BuildBot. It is shown here, not changed here." }));
    return t.node;
  }

  /* D95: the owner's one setting for files Drawbridge can't read. Only she changes it, from a paired browser. */
  async function filesSetting() {
    const box = el("div", { "data-dev": "drawbridge-files" });
    const f = await P.api("GET", "/v1/settings/drawbridge");
    if (P.missing(f)) { box.appendChild(P.later("the Drawbridge files setting (GET /v1/settings/drawbridge, D95)")); return box; }
    if (f.status !== 200) { box.appendChild(el("p", { class: "fail", text: P.said(f) })); return box; }
    const v = D.filesChoices(f.data);
    const said = el("p", { class: "small", "data-said": "drawbridge-files" });
    box.appendChild(el("p", { class: "micro", text: v.title }));
    v.choices.forEach((c) => {
      const radio = el("input", { type: "radio", name: "drawbridge-files", value: c.id, "aria-label": c.name });
      radio.checked = c.on;
      radio.addEventListener("change", async () => {
        if (!radio.checked) return;
        const x = await P.api("PUT", "/v1/settings/drawbridge", { files: c.id });
        if (x.status !== 200) { say(said, x); return; }
        said.textContent = "Saved: " + c.name + ". It counts from Drawbridge's next check.";
      });
      box.appendChild(el("label", { class: "opt", "data-files": c.id }, [radio, el("span", { class: "small" }, [el("b", { text: c.name + ". " }), c.line])]));
    });
    box.appendChild(said);
    return box;
  }

  async function drawbridgeTile() {
    const t = P.tile({ id: "drawbridge", icon: "fort", title: "Drawbridge's checks" });
    t.body.appendChild(await filesSetting());
    const r = await P.api("GET", "/v1/audit?limit=200");
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const checks = L.drawbridgeChecks(r.data.lines, 10);
    t.body.append(el("p", { class: "small muted", text: "Drawbridge checks everything that comes in and every change a tool makes, by fixed rules. No answer means no. Its rules are not changed from here." }),
      checks.length ? el("ul", { class: "lines" }, checks.map((c) => el("li", {}, [el("span", { class: "when", text: String(c.when || "").replace("T", " ").slice(0, 19) }), el("span", { text: c.text })])))
        : el("p", { class: "empty", text: "No checks in the audit log yet." }));
    return t.node;
  }

  function gearTile() {
    const t = P.tile({ id: "gear", icon: "router", title: "Your own gear" });
    const add = el("span", { text: L.signInWords(null, "", true).line });
    t.body.append(el("p", { class: "small" }, [el("b", { text: "Your firewall or router. " }), "BuildBot does not change your firewall. Your firewall keeps doing its job."]),
      el("p", { class: "small" }, [el("b", { text: "A filter on your home network " }), "(Pi-hole, AdGuard and the like) works on its own, next to your bot. BuildBot does not change it."]),
      el("p", { class: "small" }, [el("b", { text: "A proxy. " }), "Your own proxy is not used. Apps reach the internet only through the Bridge, so its checks see where every request really goes."]),
      el("p", { class: "small" }, [el("b", { text: "Add a device. " }), add]));
    P.signinWhere().then((w) => { add.textContent = L.signInWords(w, "", true).line; });     // D92
    return t.node;
  }

  async function bridgePage(sec, note, keepTile) {
    /* Toolsmith's own change draws it again. The Bridge's buttons leave its tile as it is, with any key she pasted. */
    const [now, led, ts, hosts, checks] = await Promise.all([bridgeNow((line) => bridgePage(sec, line, ts)), ledgerTile(),
      keepTile || toolsmithTile((line) => bridgePage(sec, line)), hostsTile(), drawbridgeTile()]);
    if (note && now.said) now.said.textContent = note;
    const noted = note && !now.said ? el("p", { class: "small", "data-said": "bridge", text: note }) : "";   // no Bridge tile to say it in
    clear(sec).append(back(), header("Zone 3: Bridge", "What may leave your computer, who can switch things on, Toolsmith, and your own gear.", "bridge"), noted,
      el("div", { class: "row lede", "data-dev": "goto" }, [goTo("#outside", "What leaves your computer", "Every app that may reach the internet, and what it reached."),
        goTo("#developer/github", "GitHub", "Link your GitHub for Toolsmith's searches, or add code from a GitHub project."),
        goTo("#developer/toolsmith", "Toolsmith's cloud model", "Pick a company for Toolsmith and add its key, or keep it on this computer.")]),
      el("div", { class: "tiles" }, [now.node || now, ts, led, hosts, checks, window.PuffAddresses ? window.PuffAddresses.block() : null, gearTile()]));
  }

  /* ---- GitHub alone (lane I18g), from Zone 3 Bridge ---- */
  async function githubPage(sec) {
    clear(sec).append(back(), header("GitHub", "Link your GitHub, or add code from a GitHub project for your build. " + CONSENT, "bridge"),
      el("div", { class: "tiles" }, [window.PuffGitHub ? window.PuffGitHub.block({ where: "developer" }) : P.later("the GitHub block (github.js)"),
        window.PuffSearchKeys ? window.PuffSearchKeys.block() : null, window.PuffAddApi ? window.PuffAddApi.block() : null]));
  }

  /* ---- Toolsmith's cloud model alone (D101), from Zone 3 Bridge ---- */
  async function toolsmithPage(sec) {
    const tile = await toolsmithHere();
    clear(sec).append(back(), header("Toolsmith's cloud model", "Pick a company for Toolsmith and add its key, or keep it on this computer. " + CONSENT, "bridge"),
      el("div", { class: "tiles" }, [tile]));
  }

  /* ---- the page and its parts ---- */
  const PARTS = { vault: vaultPage, brain: brainPage, bridge: bridgePage, github: githubPage, toolsmith: toolsmithPage };
  P.page("developer", {
    title: "Developer",
    async draw(sec, arg, live) {
      if (live) return;                        // the terminal keeps its own screen; no redraw from the Bus
      const part = String(arg || "").split("/")[0];
      if (part === "terminal") {
        if (/[?&]window=1/.test(location.search) || window.name === "puffpai-command-line") {   // its own window: no menu around it
          ["rail", "scrim"].forEach((id) => { const n = document.getElementById(id); if (n) n.style.display = "none"; });
        }
        clear(sec).append(back(), el("div", { class: "tiles" }, [terminalTile(true)]));
        return;
      }
      if (PARTS[part]) { await PARTS[part](sec); return; }
      await overview(sec);
    },
  });
})();
