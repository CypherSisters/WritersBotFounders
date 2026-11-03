/* Lane I18f: the Developer page's words and choices, as pure logic (no DOM), so node can check them.
 * Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/developer/page.tsx (cardsFor),
 * developer/overviewWords.ts (vaultStatus), developer/developerService.ts (vaultWords) and the three zone pages
 * (adapted to v2's routes: /v1/vault/status, /v1/model, /v1/bridge, /v1/folders, /v1/toolsmith/*). */
(function (root) {
  "use strict";

  /* The Vault card's line: what is true now, never "open" while it is locked. */
  function vaultStatus(v) {
    if (!v || typeof v !== "object" || !v.state) return "Could not be read";
    switch (String(v.state)) {
      case "open": return "Made, open now";
      case "locked": return "Made, locked";
      case "throttled": return "Made, locked: too many tries, wait a moment";
      case "not_set_up": return "Not made yet";
      case "missing": return "Made, its folder can't be found";
      case "safe_mode": return "Made, in safe mode";
      default: return "Could not be read";
    }
  }

  /* The Brain card's line from GET /v1/model. */
  function brainStatus(m) {
    if (!m || typeof m !== "object") return "Could not be read";
    const name = (m.settings && m.settings.model) || "";
    if (!name) return "No model chosen yet";
    const st = (m.state && m.state.state) || "";
    return name + ", " + (st === "ready" ? "answering" : st === "loading" ? "loading" : "not answering right now");
  }

  /* The Bridge card's line from GET /v1/bridge (its own `shown` and `search`). */
  function bridgeStatus(b) {
    if (!b || typeof b !== "object" || !b.state) return "Could not be read";
    if (b.state === "open") return "Open: something may go out now";
    if (b.setting === "open" && b.search) return "Closed, Toolsmith may search while it builds";
    return "Closed";
  }

  /* The three zone cards, in v1's order and words. */
  function zoneCards(got) {
    got = got || {};
    return [
      { id: "vault", zone: "Zone 1", name: "Vault", icon: "lock", href: "#developer/vault", status: vaultStatus(got.vault),
        more: "Where your private things are kept, how they are locked, and the folders your bot reads." },
      { id: "brain", zone: "Zone 2", name: "Brain", icon: "neurology", href: "#developer/brain", status: brainStatus(got.model),
        more: "The model your bot thinks with, where it runs, and the models for each job." },
      { id: "bridge", zone: "Zone 3", name: "Bridge", icon: "public", href: "#developer/bridge", status: bridgeStatus(got.bridge),
        more: "What may leave your computer, Toolsmith's model and key, and Drawbridge's checks." },
    ];
  }

  /* "Kept in": where the vault lives, in her words (GET /v1/vault/status `store`). */
  function vaultPlace(v) {
    const s = (v && v.store) || {};
    const where = typeof s.location === "string" ? s.location : "";
    if (v && v.state === "not_set_up") return { where: "", line: "No vault is made yet. Setup makes it." };
    if (s.ok === false) return { where: where, line: s.reason ? String(s.reason) : "Its folder can't be reached right now." };
    return { where: where, line: where ? "That folder is there." : "" };
  }

  /* What can be done to the vault from here: lock it, or open it with the PIN. */
  function vaultActions(v) {
    const st = v && v.state;
    return { lock: st === "open", unlock: st === "locked", setup: st === "not_set_up",
             wait: st === "throttled" ? Number(v.retry_after || 0) : 0 };
  }

  /* The folders (GET /v1/folders) in plain rows. */
  function foldersView(f) {
    f = f || {};
    const linked = (Array.isArray(f.linked) ? f.linked : []).filter(function (x) { return x && typeof x.path === "string"; })
      // what it read and what it couldn't, in the Core's words (D126)
      .map(function (x) { return { path: x.path, waiting: Number(x.waiting) || 0, line: x.there ? String(x.line || "There") : "Not there right now, so it is not read", files: Array.isArray(x.files) ? x.files : [] }; });
    const setup = f.from_setup && typeof f.from_setup === "object" ? f.from_setup : null;
    return {
      data: String(f.data || ""), dataLine: f.data_there === false ? "Not there yet: it is made when something is saved in it."
        : f["default"] ? "In BuildBot's own folder" : "A folder you chose",
      linked: linked, none: linked.length ? "" : "No folders are linked yet.",
      setup: setup ? { line: "Setup saved " + [setup.data ? "a data folder (" + setup.data + ")" : "",
        setup.linked && setup.linked.length ? setup.linked.length + (setup.linked.length === 1 ? " linked folder" : " linked folders") : ""]
        .filter(Boolean).join(" and ") + ", but this bot does not use them yet.", folders: (setup.linked || []).map(String) } : null,
      line: String(f.line || ""),
    };
  }

  /* One folder path as she typed it: one line, a whole path, at most 1000 characters (the Core checks again). */
  function folderPath(text) {
    const t = String(text || "").trim().replace(/^"(.*)"$/, "$1");
    if (!t) return { ok: false, line: "" };
    if (t.length > 1000 || /[\r\n]/.test(t)) return { ok: false, line: "One folder, on one line." };
    if (!/^([A-Za-z]:[\\/]|\\\\|\/|~|%[A-Za-z_]+%)/.test(t)) return { ok: false, line: "Use the folder's whole path, like C:\\Users\\you\\Documents\\Recipes." };
    return { ok: true, path: t, line: "" };
  }

  /* A file's bytes for the Core: what follows the comma in a data: address (D127). */
  function dataUrlBase64(url) {
    const s = String(url || "");
    return s.slice(s.indexOf(",") + 1);
  }

  /* The apps that take a file of this name, by its ending or text kinds (D127; the Core checks again). */
  const TEXTS = [".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml", ".log"];
  function sendFits(targets, name) {
    const m = /\.[^.\\/]+$/.exec(String(name || ""));
    const end = m ? m[0].toLowerCase() : "";
    return (Array.isArray(targets) ? targets : []).filter(function (t) {
      return (t.types || []).some(function (x) { return String(x).toLowerCase() === end || (x === "text/*" && TEXTS.indexOf(end) >= 0); });
    });
  }

  /* Where her books and downloads are (GET /v1/knowledge/place, D103), and how a move goes, in plain rows. */
  function placeView(v) {
    v = v || {};
    const m = v.move && typeof v.move === "object" ? v.move : null;
    const moving = !!m && m.phase !== "done";
    const files = moving ? Number(m.done_files || 0) + " of " + Number(m.total_files || 0) + " files" : "";
    return { where: String(v.store || ""), free: String(v.free || ""), line: String(v.line || ""), warning: String(v.warning || ""),
             isDefault: v["default"] === true, moving: moving, running: moving && m.running === true,
             percent: m ? Number(m.percent || 0) : 0, progress: moving ? files + (m.now ? ", " + m.now : "") : "",
             moveLine: m ? String(m.line || "") : "", canResume: moving && m.can_resume === true, canCancel: moving && m.can_cancel === true };
  }

  /* Toolsmith now (GET /v1/toolsmith), with its key (GET /v1/toolsmith/keys) in names only, and the vault's state
   * (GET /v1/vault/status), because "kept: null" means locked or not set up. Never a key's value: the Core sends none.
   * `stat` is the short word for the tile's corner. Can't tell is never "kept" or "no key". */
  function keyStat(kept, vault) {
    if (kept === true) return "KEY KEPT";
    if (kept === false || vault === "not_set_up") return "NO KEY";
    return vault === "locked" ? "VAULT LOCKED" : "CAN'T TELL";
  }
  function toolsmithNow(st, keys, vault) {
    st = st || {};
    const list = (Array.isArray(keys) ? keys : []).filter(Boolean);
    const noVault = vault === "not_set_up";
    if (st.where !== "cloud") {
      const kept = list.filter(function (x) { return x.kept === true; }).map(function (x) { return x.company || x.provider; });
      const unknown = !list.length || list.some(function (x) { return x.kept !== true && x.kept !== false; });
      const state = kept.length ? true : unknown ? null : false;
      const key = kept.length ? "A key is kept for " + kept.join(", ") + ". Toolsmith does not use it while it is on this computer."
        : noVault ? "No key is kept. Set up the vault first, so a key has a safe home."
        : !list.length ? "Can't tell right now whether a key is kept."
        : unknown ? "Open your vault to see whether a key is kept."
        : "No key is kept. This computer needs none.";
      return { cloud: false, line: "This computer's own model", cap: null, key: key, stat: keyStat(state, vault) };
    }
    const k = list.find(function (x) { return x.provider === st.provider; }) || null;
    const state = k && (k.kept === true || k.kept === false) ? k.kept : null;
    const key = state === true ? "Its key is kept in your vault."
      : state === false ? "No key is kept yet: Toolsmith waits for it before its first run."
      : noVault ? "No key is kept yet. Set up the vault first, so its key has a safe home."
      : k ? "Open your vault to see whether its key is kept." : "Can't tell right now whether its key is kept.";
    return { cloud: true, line: (st.company || st.provider || "") + ", " + (st.model || ""), cap: Number(st.cost_cap_usd || 0), key: key,
             stat: keyStat(state, vault) };
  }

  /* A cost cap change: lower or the same applies at once; higher is a widening and needs her Yes again. */
  function capChange(now, next) {
    const n = Number(next);
    if (next === "" || next === null || next === undefined || !isFinite(n) || n < 0) return "bad";
    if (now === null || now === undefined) return "higher";
    return n > Number(now) ? "higher" : n === Number(now) ? "same" : "lower";
  }

  /* The hosts Toolsmith may reach (GET /v1/toolsmith/hosts), as lines. */
  function hostLines(h) {
    h = h || {};
    const out = [];
    const s = h.search || {};
    out.push({ what: "Search for apps and knowledge, while it builds", hosts: (s.hosts || []).join(", "),
               state: s.on ? "On" : s["switch"] ? "On, opens when the Bridge is open" : "Off" });
    const c = h.cloud || {};
    out.push({ what: "Its cloud model", hosts: c.on ? (c.hosts || []).join(", ") : "", state: c.on ? (c.company || "On") : "Off: it uses this computer" });
    const k = h.knowledge || {};
    out.push({ what: "Knowledge on a plan you said Yes to", hosts: (k.hosts || []).join(", "), state: "Only for that build" });
    return out;
  }

  /* "puffpai" is on no PATH (D92(e)): say so, so a copied command is not a dead end. */
  const CLI_WHERE = "Type puffpai as the full path to this install's own program, not by itself: it is not on your PATH.";
  /* The commands for each Developer page, only ones this build has ("Use CLI"). */
  const CLI = {
    overview: [["Check a package folder", "puffpai check <folder>"], ["Add your own folder, run where it is", "puffpai add <folder> --link"],
               ["Sign in another browser on this computer", "puffpai pair"], ["What runs now", "puffpai list"]],
    vault: [["What the Desktop shows, the vault's cards too", "puffpai notices"],
      ["Where your books and downloads are", "puffpai knowledge where"], ["Move them", "puffpai knowledge move <folder>"]],
    brain: [["What runs now", "puffpai list"], ["One app's log", "puffpai logs <id>"]],
    bridge: [["The Bridge now", "puffpai bridge status"], ["Open it", "puffpai bridge open"], ["Close it", "puffpai bridge close"]],
  };
  function cliCommands(page) {
    return (CLI[page] || []).map(function (c) { return { what: c[0], command: c[1], fill: c[1].indexOf("<") >= 0 }; });
  }
  const CLI_NOTE = "Open PowerShell, then paste. No command here carries a PIN or a key.";

  /* D95: the owner's one setting for files Drawbridge can't read (GET /v1/settings/drawbridge), as plain
   * choices: Normal (the default) or Locked down. Anything else the Core sends is left out. */
  function filesChoices(got) {
    const g = got && typeof got === "object" ? got : {};
    const now = g.files === "normal" || g.files === "locked" ? g.files : "";
    const choices = (Array.isArray(g.choices) ? g.choices : [])
      .filter(function (c) { return c && (c.id === "normal" || c.id === "locked"); })
      .map(function (c) { return { id: c.id, name: String(c.name || c.id), line: String(c.line || ""), on: c.id === now }; });
    return { title: String(g.title || "Files Drawbridge can't read"), now: now, choices: choices };
  }

  /* The census (GET /v1/model/menu) as v1's "This computer" line. */
  function computerLine(census) {
    const c = census || {};
    const mem = c.memory || {}, cpu = c.cpu || {}, acc = c.accelerator || {};
    const parts = [mem.total_gb ? mem.total_gb + " GB of memory" : "", cpu.model || "", cpu.logical_cores ? cpu.logical_cores + " cores" : "",
                   acc.name && acc.kind !== "none" && acc.kind !== "unknown" ? String(acc.name) : ""];
    return parts.filter(Boolean).join(", ") || "Could not be read";
  }

  const api = { vaultStatus: vaultStatus, brainStatus: brainStatus, bridgeStatus: bridgeStatus, zoneCards: zoneCards, vaultPlace: vaultPlace,
                vaultActions: vaultActions, foldersView: foldersView, folderPath: folderPath, dataUrlBase64: dataUrlBase64, sendFits: sendFits, placeView: placeView, toolsmithNow: toolsmithNow, capChange: capChange,
                hostLines: hostLines, cliCommands: cliCommands, CLI_NOTE: CLI_NOTE, CLI_WHERE: CLI_WHERE, computerLine: computerLine,
                filesChoices: filesChoices };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffDev = api;
})(typeof window !== "undefined" ? window : this);
