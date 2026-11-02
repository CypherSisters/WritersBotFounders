/* PuffPAI Desktop: the pure logic (no DOM, no network), so it can be checked headless with node.
 *
 * Everything the Desktop draws comes from the Core API (GET /v1/registry and friends).
 * Nothing here knows any one package: tray items, pages and widgets come from each
 * Registry row's `ui` block (02 "Field reference", D3, D13).
 *
 * Package pages, widgets and icons live on each package's own origin (D53,
 * http://<id>.localhost:8422), never on the Core's own address. `base` below is the pages
 * address from GET /v1/settings "pages" (http://127.0.0.1:8422); pkgOrigin() turns it into a
 * package's own origin, keeping its scheme and port. Pages are framed with PAGE_SANDBOX, which
 * may include allow-same-origin because that origin is the package's own, never the Core's.
 * Without a pages address, no package link is made at all.
 *
 * On the home network (D130) `base` is { scheme, host, ports: { <id>: <port> } } (networkBase): a package's own
 * origin is its own port on the address this device uses, because <id>.localhost would mean the device itself.
 */
(function (root) {
  "use strict";

  const WIDGET_TYPES = ["list", "number", "image", "feed", "html"];
  const PAGE_SANDBOX = "allow-scripts allow-forms allow-popups allow-same-origin";
  const WIDGET_SANDBOX = "allow-scripts";
  const PACKAGE_ID = /^[a-z][a-z0-9-]{1,39}$/;

  /* D130: true when `base` is the home network's. */
  function isNetwork(base) { return !!base && typeof base === "object" && !!base.ports && typeof base.ports === "object"; }
  /* The home network's `base`, from this page's location and GET /v1/network/pages. "" when it did not answer. */
  function networkBase(loc, data) {
    if (!loc || !data || typeof data.ports !== "object" || !data.ports) return "";
    return { scheme: String(loc.protocol), host: String(loc.hostname), ports: data.ports };
  }
  /* A package's own origin (D53): http://<id>.localhost:<pages port>. Null when unknown. */
  function pkgOrigin(id, base) {
    if (typeof id !== "string" || !PACKAGE_ID.test(id)) return null;
    if (isNetwork(base)) {
      const port = Object.prototype.hasOwnProperty.call(base.ports, id) ? base.ports[id] : null;
      return Number.isInteger(port) ? base.scheme + "//" + base.host + ":" + port : null;
    }
    if (typeof base !== "string") return null;
    const m = /^(https?:)\/\/[^/:?#]+(:\d+)?\/*$/.exec(base);
    if (!m) return null;
    return m[1] + "//" + id + ".localhost" + (m[2] || "");
  }

  function pkgUrl(id, page, base) {
    const o = pkgOrigin(id, base);
    if (!o) return null;
    if (page.path) return o + (page.path.charAt(0) === "/" ? page.path : "/" + page.path);
    if (page.file) return o + "/" + page.file.replace(/^\/+/, "");
    return null;
  }

  /* A /pkg/<id>/... path from the Core (an avatar preview) on that package's own origin. */
  function onPages(path, base) {
    if (typeof path !== "string") return null;
    const m = /^\/pkg\/([^/]+)\/(.*)$/.exec(path);
    if (!m) return null;
    const o = pkgOrigin(m[1], base);
    return o ? o + "/" + m[2] : null;
  }

  function isAdopted(row) {
    return typeof row.origin === "string" && row.origin.indexOf("adopted:") === 0;
  }

  /* Where a page opens (D13, D83): PuffPAI's own origin (the Core's answer, GET /v1/registry `own`: built in or from
   * the PuffPAI store) -> inside the Desktop; anything else -> a new tab, always. A page with its own `url` opens there. */
  function pageTarget(row, page, base) {
    if (isNetwork(base)) {        // D130: adopted apps, tries and programs stay on the bot's computer
      if (!page && row.ui && row.ui.open) return { kind: "computer", page: null, note: ON_COMPUTER };
      if (page && (isAdopted(row) || (page.url && !networkOpen(page.url, base).url))) return { kind: "computer", page: page.id, note: ON_COMPUTER };
    }
    if (!page) {
      if (row.ui && row.ui.open) return { kind: "program", page: null, note: "Opens in its own window" };
      return { kind: "none" };
    }
    if (page.url) return { kind: "tab", url: page.url, page: page.id };
    const src = pkgUrl(row.id, page, base);
    if (!src) return { kind: "none" };
    if (row.own !== true || page.show === "new_tab") return { kind: "tab", url: src, page: page.id };
    return { kind: "desktop", src: src, page: page.id };
  }

  /* D130: a link the Core made, as it means on another device. A package's <id>.localhost page goes to its own port;
   * 127.0.0.1 and localhost would mean the device itself, so they get a plain line and no link. */
  const ON_COMPUTER = "This opens only on the computer running your bot.";
  function networkOpen(url, base) {
    if (!isNetwork(base)) return { url: String(url) };
    let u;
    try { u = new URL(String(url)); } catch (e) { return { url: null, line: ON_COMPUTER }; }
    const host = u.hostname.toLowerCase();
    const own = /^([a-z][a-z0-9-]{1,39})\.localhost$/.exec(host);
    if (own) {
      const o = pkgOrigin(own[1], base);
      return o ? { url: o + u.pathname + u.search + u.hash } : { url: null, line: ON_COMPUTER };
    }
    if (host === "127.0.0.1" || host === "localhost" || host === "[::1]" || host.endsWith(".localhost")) return { url: null, line: ON_COMPUTER };
    return { url: String(url) };
  }
  /* D130: True on the home network, known from this page's own address before the Core has said anything. */
  function onNetworkHost(hostname) {
    const h = String(hostname || "").toLowerCase();
    return !(h === "localhost" || h === "127.0.0.1" || h === "[::1]" || h.endsWith(".localhost"));
  }
  /* D134: a file sent from this device (POST /v1/knowledge/upload). The Core checks again; this only says so sooner. */
  const UPLOAD_MAX = 50 * 1024 * 1024;
  const UPLOAD_TYPES = ".txt,.md,.docx,.pdf";
  const UPLOAD_TOO_BIG = "That file is too big to send. The most is 50 MB.";
  function uploadProblem(name, size) {
    const end = /\.[^.]+$/.exec(String(name || ""));
    if (!end || UPLOAD_TYPES.split(",").indexOf(end[0].toLowerCase()) < 0) return "PuffPAI can pin .txt, .md, .docx and .pdf files.";
    if (size > UPLOAD_MAX) return UPLOAD_TOO_BIG;
    return size === 0 ? "That file is empty, so nothing was saved." : "";
  }
  /* The Settings row for the home network (GET /v1/network). *here*: this page is on the bot's own computer. */
  function networkView(data, here) {
    const d = data && typeof data === "object" ? data : {};
    const on = d.on === true;
    const line = !on ? "Off. Only this computer can open BuildBot. Turn it on and it listens on your home network: a device there gets a pair screen, and only a device you pair gets in."
      : d.open === false ? String(d.line || "On, but not listening right now.")
        : "On. BuildBot listens on your home network, and only a device you pair gets in. On another device, open one of these addresses and type a pairing code the first time. Adopted apps and what Toolsmith tries stay on this computer.";
    return { on, note: on ? "On, paired devices only" : "Off, this computer only", line, urls: on && Array.isArray(d.urls) ? d.urls.map(String) : [],
             worth: String(d.worth_knowing || ""), button: on ? "Turn it off" : "Turn it on",
             how: here ? "The first time BuildBot listens, Windows may ask whether to allow Python through its firewall. Choose Allow for private networks." : "Turning it off from here also cuts off this device. It stays off until someone turns it on at the bot's own computer." };
  }

  /* What an item's button says: only an outside app (adopted, origin adopted:) says "Open in a new tab"
   * (02 "Adopted packages"). Any other page, a new-tab one too, keeps its own label (null here). */
  function openLabel(target, row) {
    if (!target) return null;
    if (target.kind === "tab") return row && isAdopted(row) ? "Open in a new tab" : null;
    if (target.kind === "program") return "Open";
    return null;
  }

  /* Opening the Bridge (I2b decision): in safe mode it is floor-level. The card says the safe-mode
   * reason first, then asks for the PIN (D29). *view* is GET /v1/bridge or a refused open's answer. */
  const BRIDGE_PIN_LINE = "Opening the Bridge now needs your PIN. Safe mode itself lasts until you restart BuildBot.";
  function bridgeOpen(view) {
    const sm = (view && view.safe_mode) || {};
    if (sm.on !== true && !(view && view.needs_pin)) return { needsPin: false, title: null, lines: [] };
    const reasons = (sm.reasons || []).filter(function (r) { return typeof r === "string" && r; });
    return { needsPin: true, title: "BuildBot is in safe mode",
             lines: (reasons.length ? reasons : ["A safety check failed."]).concat([BRIDGE_PIN_LINE]) };
  }

  /* The Bridge in her words (GET /v1/bridge `said`/`state`, the Core's Bridge.view, the same as
   * puffpai bridge status) and its one button. `state` reads open only when something can go out;
   * `setting` is the Bridge's own open or closed. While it allows only Toolsmith's build-time search it
   * reads closed, so the button turns that search off. Set open with nothing let through it reads closed,
   * and still offers Close (lane I8b, verifier note): the setting itself is open, so she can shut it. */
  function bridgeView(view) {
    const v = view || {};
    const said = (v.said || []).filter(function (l) { return typeof l === "string" && l; });
    const open = v.state === "open";
    const setting = v.setting || v.state;
    const text = said.length ? said.join(" ") : open ? "The Bridge is open." : "The Bridge is closed.";
    if (open) return { text: text, button: "Close the Bridge", action: "close" };
    if (setting !== "open") return { text: text, button: "Open the Bridge", action: "open" };
    if (v.search) return { text: text, button: "Turn off Toolsmith's search", action: "close" };
    return { text: text, button: "Close the Bridge", action: "close" };
  }

  function iconUrl(row, icon, base) {
    if (!icon) return null;
    if (/^[a-z][a-z0-9+.-]*:/i.test(icon) || icon.indexOf("//") === 0) return null;   // icons live in the package
    const o = pkgOrigin(row.id, base);
    return o ? o + "/" + icon.replace(/^\/+/, "") : null;
  }

  /* One tray item per declared tray entry; a package with pages or ui.open but no tray entry
   * still gets one item with its name (03 step 11: one item per package). */
  function trayItems(rows, base) {
    const out = [];
    (rows || []).forEach(function (row) {
      if (row.status === "removed") return;
      const ui = row.ui || {};
      const pages = ui.pages || [];
      const tray = ui.tray || [];
      if (["ui", "avatar", "look", "wizard", "bundle"].indexOf(row.kind) >= 0) return;   // drawn elsewhere, not tray items
      const entries = tray.length ? tray : ((pages.length || ui.open) ? [{ label: row.name, opens: pages.length ? pages[0].id : null }] : []);
      entries.forEach(function (t, i) {
        const page = pages.find(function (p) { return p.id === t.opens; }) || (t.opens ? null : pages[0]) || null;
        out.push({
          key: row.id + ":" + (t.opens || i),
          pkg: row.id,
          label: t.label || row.name,
          icon: iconUrl(row, t.icon || null, base),
          status: row.status,
          down: !(row.health && row.health.ok),
          target: pageTarget(row, page, base),
          openLabel: openLabel(pageTarget(row, page, base), row),
        });
      });
    });
    return out;
  }

  function widgetsOf(rows, base) {
    const out = [];
    (rows || []).forEach(function (row) {
      ((row.ui || {}).widgets || []).forEach(function (w) {
        if (WIDGET_TYPES.indexOf(w.type) < 0) return;
        if (w.type === "html" && row.own !== true) return;      // an html widget is a frame (D83)
        out.push({ key: row.id + ":" + w.id, pkg: row.id, pkgName: row.name, type: w.type, title: w.title,
                   src: pkgUrl(row.id, w, base) });
      });
    });
    return out;
  }

  const BAD_WIDGET = "This widget sent something the Desktop can't show.";
  const OUTSIDE_PICTURE = "This picture is outside your computer.";

  /* An image widget's picture, only from its own package's origin (D53): a picture from anywhere
   * else would be fetched by the browser, around the Bridge. It may be given as its own origin's
   * URL or as a /pkg/<its id>/ path. Null means "do not load". */
  function ownPicture(src, ctx) {
    if (typeof src !== "string" || !ctx || !ctx.pkg || !ctx.base) return null;
    const own = pkgOrigin(ctx.pkg, ctx.base);
    if (!own) return null;
    const prefix = "/pkg/" + ctx.pkg + "/";
    let rest;
    if (src.indexOf(own + "/") === 0) rest = src.slice(own.length + 1);
    else if (src.indexOf(prefix) === 0) rest = src.slice(prefix.length);
    else return null;
    rest = rest.split(/[?#]/)[0];
    if (!rest || /^\/|(^|\/)\.{1,2}(\/|$)|\/\/|\\|%2e|%2f|%5c|@/i.test(rest)) return null;   // no way out of its folder
    return own + "/" + rest;
  }

  /* A widget's JSON, checked against its type's shape (02 "ui.widgets"). ctx: { pkg, base }. */
  function widgetModel(type, data, ctx) {
    if (!data || typeof data !== "object") return { error: BAD_WIDGET };
    if (type === "list") {
      if (!Array.isArray(data.items)) return { error: BAD_WIDGET };
      return { items: data.items.slice(0, 50).map(function (it) { return String(it && it.text != null ? it.text : it); }) };
    }
    if (type === "number") {
      if (data.value === undefined || data.value === null) return { error: BAD_WIDGET };
      return { value: String(data.value), unit: data.unit ? String(data.unit) : "", label: data.label ? String(data.label) : "" };
    }
    if (type === "image") {
      if (typeof data.src !== "string") return { error: BAD_WIDGET };
      const caption = data.caption ? String(data.caption) : "";
      const src = ownPicture(data.src, ctx);
      if (!src) return { src: null, outside: true, note: OUTSIDE_PICTURE, caption: caption };
      return { src: src, caption: caption };
    }
    if (type === "feed") {
      if (!Array.isArray(data.items)) return { error: BAD_WIDGET };
      return { items: data.items.slice(0, 30).map(function (it) {
        const url = typeof it.url === "string" && /^https?:\/\//.test(it.url) ? it.url : null;
        return { title: String(it.title || ""), url: url, time: it.time ? String(it.time) : "" };
      }) };
    }
    return { error: BAD_WIDGET };
  }

  /* Who raised a step, in plain words (D49: the card always says). */
  function raisedBy(step) {
    const by = typeof step.raised_by === "string" ? step.raised_by : "";
    if (by.indexOf("pkg:") === 0) return "Raised by the package " + by.slice(4);
    if (by.indexOf("setup_steps:") === 0) return "From " + by.slice(12) + "'s setup steps, which you said Yes to";
    if (by === "owner") return "Raised with your owner key";
    if (by === "core" || (!by && (!step.package || step.package === "core"))) return "Raised by BuildBot";
    return "Raised by " + (by || step.package);
  }

  /* "Your turn" card (01 "Checklist", 03 3.5): Copy, Done, Skip, plus Run as administrator.
   * Running as administrator needs the PIN (D29); the card shows the exact command first,
   * and never offers it for a step a package raised itself (D49). */
  function stepCard(step) {
    const buttons = [];
    const copyable = step.kind === "run_command" && step.text;
    const fromPackage = typeof step.raised_by === "string" && step.raised_by.indexOf("pkg:") === 0;
    const admin = !!(copyable && step.admin && !fromPackage && (step.shell || "powershell") === "powershell");
    if (copyable) buttons.push({ id: "copy", label: "Copy" });
    if (admin) buttons.push({ id: "admin", label: "Run as administrator, with my PIN" });
    if (step.kind === "open_url") buttons.push({ id: "open", label: "Open", url: step.get_it || step.text });
    if (step.kind === "enter_secret" && step.get_it) buttons.push({ id: "open", label: "Get it", url: step.get_it });
    if (step.kind === "enter_secret") buttons.push({ id: "save", label: "Save to vault" });
    buttons.push({ id: "done", label: "Done" });
    buttons.push({ id: "skip", label: "Skip" });
    return {
      id: step.id,
      title: step.title,
      why: step.why || "",
      from: step.package && step.package !== "core" ? step.package : "",
      raisedBy: raisedBy(step),
      needsPin: admin,
      shell: step.kind === "run_command" ? (step.shell || "powershell") : "",
      text: copyable ? step.text : "",
      secret: step.kind === "enter_secret" ? { key: step.key } : null,
      failed: step.check_failed || "",
      buttons: buttons,
    };
  }

  /* A Yes card (consent), from GET /v1/consent: "title" and "lines" are the Core's one plain view of the
     stored card (what its card_sha covers: what it is, hosts, keys, setup steps, changing tools, Drawbridge's
     notes and what it couldn't check). Floor-level cards ask for the PIN, never a tap (D29). A card that
     came with no details offers only No: a Yes is never given to something she can't read. */
  const PIN_LINE = "This one needs your PIN. A tap is not enough.";

  /* Tick boxes on two Toolsmith cards (lane I4b's routes), all unticked to start. Her ticks are sent
   * before her Yes; a No sends nothing.
   * - The plan card (card.covers.card.offers): an installed app that may cover a job. Ticked: that job uses
   *   it instead of searching. PUT /v1/toolsmith/builds/<build>/use {consent_id, use: [job numbers]}.
   * - A job's hold card (card.try_holds): apps Drawbridge couldn't fully check. Ticked: Toolsmith may try
   *   it. PUT /v1/toolsmith/builds/<build>/holds/<consent id> {try: [candidate refs]}. The ticks are always
   *   sent, so a Yes with nothing ticked tries none of them (the card says so). */
  const OFFER_NOTE = "Tick an app to use it for that job instead of searching. Nothing ticked: every job searches as planned.";
  const HOLD_NOTE = "Tick the apps Toolsmith may try. With nothing ticked, your Yes lets Toolsmith try none of them.";
  /* Her ticks not sent yet, by consent id, so a redraw of the Yes cards (on every consent event) keeps
   * them. keep(ids) drops the cards that are gone; drop(id) the one she answered. */
  function TickMemory() { this.by = {}; }
  TickMemory.prototype.get = function (id) { return (this.by[id] || []).slice(); };
  TickMemory.prototype.has = function (id, value) { return (this.by[id] || []).indexOf(value) >= 0; };
  TickMemory.prototype.set = function (id, value, on) {
    const now = (this.by[id] || []).filter(function (v) { return v !== value; });
    if (on) now.push(value);
    this.by[id] = now;
  };
  /* D99: a tick the card itself starts with (a download), once, so her own untick stays. */
  TickMemory.prototype.seed = function (id, value) {
    this.seeded = this.seeded || {};
    const key = id + "\n" + value;
    if (!this.seeded[key]) { this.seeded[key] = true; this.set(id, value, true); }
  };
  TickMemory.prototype.drop = function (id) { delete this.by[id]; };
  TickMemory.prototype.keep = function (ids) {
    const live = {};
    (ids || []).forEach(function (id) { live[id] = true; });
    Object.keys(this.by).forEach(function (id) { if (!live[id]) delete this.by[id]; }, this);
  };

  function cardTicks(c) {
    const card = (c && c.card) || {};
    const id = c && (c.id || c.consent_id);
    const covers = card.covers || {};
    const plan = covers.card || {};
    /* Lane I17: a coding or picture model the plan offers (card.covers.card.model_offers), ticked as
     * "model:<role>" in the same use list. Ticked: the Core downloads it (its own Yes card) and sets the role
     * before the build. */
    const models = (Array.isArray(plan.model_offers) ? plan.model_offers : []).filter(function (m) {
      return m && typeof m.value === "string" && m.value.indexOf("model:") === 0;
    });
    /* D99: the downloads the plan offers (card.covers.card.knowledge_offers), in the same use list. */
    const downloads = (Array.isArray(plan.knowledge_offers) ? plan.knowledge_offers : []).filter(function (d) {
      return d && typeof d.value === "string" && d.value.indexOf("knowledge:") === 0;
    });
    if (((Array.isArray(plan.offers) && plan.offers.length) || models.length || downloads.length) && typeof covers.build === "string") {
      const offers = (Array.isArray(plan.offers) ? plan.offers : []).filter(function (o) { return o && typeof o.job === "number"; });
      const shop = offers.some(function (o) { return o.store === true; });
      const note = (offers.length ? (shop ? OFFER_NOTE + " " + STORE_TICK_NOTE : OFFER_NOTE) : "")
        + (models.length ? (offers.length ? " " : "") + MODEL_TICK_NOTE : "")
        + (downloads.length ? ((offers.length || models.length) ? " " : "") + DOWNLOAD_TICK_NOTE : "");
      return {
        kind: "offers", note: note, build: covers.build,
        route: "/v1/toolsmith/builds/" + encodeURIComponent(covers.build) + "/use",
        items: offers.map(function (o) {
          return { value: o.job, label: o.line || ((o.job_name || "Job " + o.job) + ": use " + (o.name || o.package)),
                   store: o.store === true, package: typeof o.package === "string" ? o.package : "",
                   name: String(o.name || o.package || ""), jobName: String(o.job_name || "") };
        }).concat(models.map(function (m) {
          return { value: m.value, label: String(m.line || ""), store: false, model: true, package: "",
                   name: String(m.label || m.model || ""), jobName: "" };
        })).concat(downloads.map(function (d) {
          return { value: d.value, label: String(d.line || ""), store: false, ticked: d.ticked === true, package: "",
                   name: "", jobName: "" };
        })),
        body: function (ticked) { return { consent_id: id, use: ticked.slice() }; },
      };
    }
    if (Array.isArray(card.try_holds) && card.try_holds.length && typeof card.build === "string") {
      return {
        kind: "holds", note: HOLD_NOTE,
        route: "/v1/toolsmith/builds/" + encodeURIComponent(card.build) + "/holds/" + encodeURIComponent(id),
        items: card.try_holds.filter(function (h) { return h && typeof h.candidate === "string"; }).map(function (h) {
          return { value: h.candidate, label: (h.name || h.candidate) + (h.url ? " (" + h.url + ")" : "") };
        }),
        body: function (ticked) { return { try: ticked.slice() }; },
      };
    }
    return null;
  }
  /* Lane I16: a PuffPAI store app ticked on the plan card is added before the build, with its own Yes card.
   * The Core does it for every path (toolsmith_host/store_phase.py) and keeps the progress on the build
   * (GET /v1/toolsmith/builds/<id> "store_adds"); the Desktop only shows it. */
  const STORE_TICK_NOTE = "Your tick adds it first, with its own Yes card, instead of searching.";
  const MODEL_TICK_NOTE = "Tick a model to download it first, with its own Yes card. Nothing ticked: the bot uses its chat model.";
  const DOWNLOAD_TICK_NOTE = "Ticked downloads start after your Yes, one at a time. Untick any you do not want.";
  const STORE_LATER = "You can add it later from the PuffPAI store on your Desktop.";
  /* The plan card a Yes starts a build for: {build, consentId}, or null for any other card. */
  function planOf(rec) {
    const covers = (rec && (rec.covers || (rec.card && rec.card.covers))) || {};
    if (typeof covers.build !== "string" || !covers.build || !covers.plan_sha) return null;
    return { build: covers.build, consentId: String(rec.id || rec.consent_id || "") };
  }
  /* A build's store adds, from the Core (GET /v1/toolsmith/builds/<id>): each app's line, the card waiting
   * for an answer (asking), and done once the build started (or nothing was ticked). */
  function storeProgress(b) {
    b = b || {};
    const rec = b.store_adds && typeof b.store_adds === "object" ? b.store_adds : null;
    const started = !!b.yes;
    if (!rec) return { lines: started ? [STORE_STARTED] : [], asking: "", done: started, adding: false, record: false };
    const apps = Array.isArray(rec.apps) ? rec.apps : [];
    const lines = apps.map(function (a) { return String((a && a.line) || ""); }).filter(Boolean);
    if (rec.line) lines.push(String(rec.line));
    const ask = apps.find(function (a) { return a && a.state === "asking" && typeof a.card === "string" && a.card; });
    const adding = rec.state === "adding" || rec.state === "starting";   // done only after the launch
    return { lines: lines, asking: ask ? ask.card : "", done: !adding, adding: adding, record: true };
  }
  const STORE_STARTED = "Toolsmith started the build.";
  /* An install's end, from its Registry row: final once it is not "installing" (puffpai add's rule); working
   * only running, stopped or waiting for a step; "broken" (or gone) is not. */
  function installState(row) {
    if (!row || row.status === "removed") return { final: false, ok: false, line: "" };
    if (row.status === "installing") return { final: false, ok: false, line: "" };
    const ok = ["running", "stopped", "needs_you"].indexOf(row.status) >= 0;
    const why = row.health && row.health.reason ? String(row.health.reason).replace(/\.$/, "") : "it did not start";
    return { final: true, ok: ok, line: ok ? "" : String(row.name || row.id) + " was installed but does not work (" + why + ")." };
  }

  const NO_DETAIL_LINE = "This card came with no details, so only No is offered here.";
  function consentCard(c) {
    const lines = Array.isArray(c.lines) ? c.lines.map(String) : (c.text ? [String(c.text)] : []);
    const floor = !!c.floor;
    if (floor && lines.indexOf(PIN_LINE) < 0) lines.push(PIN_LINE);
    const readable = lines.some(function (l) { return l !== PIN_LINE; });
    if (!readable) lines.push(NO_DETAIL_LINE);
    const no = { id: "no", label: "No" };
    const ticks = readable ? cardTicks(c) : null;
    return {
      ticks: ticks,
      plan: readable ? planOf(c) : null,        // a plan card: a Yes starts its build (lane I16)
      id: c.id || c.consent_id,
      cardSha: typeof c.card_sha === "string" ? c.card_sha : null,   // which card the answer is for
      title: c.title || "Your Yes is needed",
      lines: lines,
      floor: floor,
      needsPin: floor && readable,
      pending: !c.status || c.status === "pending",
      buttons: !readable ? [no] : floor ? [{ id: "yes", label: "Yes, with my PIN" }, no] : [{ id: "yes", label: "Yes" }, no],
    };
  }

  function adminAnswer(pin) {
    return { factor: "pin", pin: pin || "" };
  }

  /* An answer always names the card it answers (card_sha), so it can't be moved to another card. */
  function consentAnswer(card, yes, pin) {
    const sha = card.cardSha ? { card_sha: card.cardSha } : {};
    if (!yes) return Object.assign({ answer: "no", factor: "tap" }, sha);
    if (card.needsPin) return Object.assign({ answer: "yes", factor: "pin", pin: pin || "" }, sha);
    return Object.assign({ answer: "yes", factor: "tap" }, sha);
  }

  function healthText(row) {
    const h = row.health || {};
    if (h.ok) return "Working";
    if (!h.checked_at) return "Not checked yet";
    return h.reason || "Not working";
  }

  function installedRows(rows, base) {
    return (rows || []).filter(function (r) { return r.status !== "removed"; }).map(function (r) {
      const pages = (r.ui || {}).pages || [];
      const t = isAdopted(r) && pages.length ? pageTarget(r, pages[0], base) : null;
      // A package with no process of its own (the Core serves its files) is "ready", not "stopped".
      const status = r.status === "stopped" && r.process === false ? "ready" : r.status === "needs_you" ? "needs you" : r.status;
      return { id: r.id, name: r.name, kind: r.kind, status: status, health: healthText(r), ok: !!(r.health && r.health.ok),
               adopted: isAdopted(r), description: r.description || "",
               tab: t && t.kind === "tab" ? t.url : null };       // an adopted app: "Open in a new tab"
    });
  }

  /* The browser wizard keeps what is on the page as a transcript of engine screens.
   * It draws only what the engine sends. After a screen with `clear`, the shown-once value
   * is removed from the page (03 1.3), and a new code takes the old one's place (D106).
   * Hidden fields draw a password box: what is typed
   * shows as dots, so it never becomes page text. */
  function WizardTranscript() {
    this.items = [];
  }
  WizardTranscript.prototype.push = function (screen) {
    if (screen.clear || screen.show) {
      this.items.forEach(function (it) { if (it.show) it.show = { removed: true }; });
    }
    this.items.push({
      title: screen.title, status: screen.status, lines: (screen.lines || []).slice(),
      answered: (screen.answered || []).map(function (a) { return a.label + ": " + a.shown; }),
      show: screen.show ? { value: screen.show.value, line: screen.show.line } : null,
      field: screen.field || null, error: screen.error || "",
    });
  };
  WizardTranscript.prototype.typed = function (field, value) {
    if (!field || field.hidden) return;              // a password box shows dots only
    this.items.push({ typed: String(value) });
  };
  WizardTranscript.prototype.visibleText = function () {
    const out = [];
    this.items.forEach(function (it, i, all) {
      if (it.typed !== undefined) { out.push(it.typed); return; }
      out.push(it.title);
      it.lines.forEach(function (l) { out.push(l); });
      it.answered.forEach(function (a) { out.push(a); });
      if (it.show && !it.show.removed) { out.push(it.show.value); out.push(it.show.line); }
      const last = i === all.length - 1 || all.slice(i + 1).every(function (x) { return x.typed !== undefined; });
      if (last && it.field) {
        const f = it.field;
        out.push(f.label);
        if (f.help) out.push(f.help);
        if (f.example_line) out.push(f.example_line);
        (f.options || []).forEach(function (o) { out.push(o.label + (o.note ? " (" + o.note + ")" : "")); });
        if (f.hint) out.push(f.hint);
      }
      if (it.error) out.push(it.error);
    });
    return out;
  };

  function wizardInput(field) {
    if (!field) return null;
    if (field.type === "choice") return { kind: "choice", options: field.options || [], value: field.default };
    if (field.type === "theme") return { kind: "theme", options: field.options || [], value: field.default };   // swatches (lane I16)
    if (field.type === "confirm") return { kind: "confirm" };
    if (field.hidden) return { kind: "password" };
    if (field.type === "number") return { kind: "number", value: field.default };
    return { kind: "text", value: field.default || "", placeholder: field.example || "" };
  }

  /* ================= lane I8b: the v1 dashboard's pages, on v2's Core API =================
   * Harvested in part from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/_lib/menuAside.ts (the rail),
   * css/_theme.css (day 07:00 to 19:00), dashboard/page.tsx (the arrange tray). Adapted. */

  /* Day 07:00 to 19:00, night otherwise (v1 _theme.css). *choice*: "auto" (or anything else), "day", "night". */
  function themeFor(hour, choice) {
    if (choice === "day" || choice === "night") return choice;
    const h = Number(hour);
    return h >= 7 && h < 19 ? "day" : "night";
  }

  /* The rail (v1 menuAside.ts), one entry per page. group: primary, secondary, settings.
   * Only pages every bot has (lane I10a). Any other page comes with a package, as its own tray item
   * under "Your apps", so a bot without that package never shows it. */
  const RAIL = [
    { id: "home", label: "Home", icon: "space_dashboard", group: "primary" },
    { id: "chat", label: "Chat", icon: "chat_bubble", group: "primary" },
    { id: "apps", label: "Apps", icon: "apps", group: "primary" },
    { id: "building", label: "Building", icon: "construction", group: "primary" },
    { id: "yourturn", label: "Your turn", icon: "task_alt", group: "primary", badge: "yourturn" },
    { id: "knowledge", label: "Knowledge", icon: "menu_book", group: "secondary" },
    { id: "vault", label: "Vault", icon: "lock", group: "secondary" },
    { id: "people", label: "People", icon: "group", group: "secondary" },
    { id: "agents", label: "Agent Team", icon: "groups", group: "secondary" },
    { id: "outside", label: "What leaves your computer", icon: "public", group: "secondary" },
    { id: "setup", label: "Setup", icon: "rocket_launch", group: "settings", badge: "setup" },
    { id: "looks", label: "Looks", icon: "palette", group: "settings" },
    { id: "developer", label: "Developer", icon: "code", group: "settings" },
    { id: "settings", label: "Settings", icon: "settings", group: "settings" },
  ];
  /* *opts.setupDone*: once setup is finished, Setup leaves the rail (Settings has it). */
  function railGroups(rail, opts) {
    const out = { primary: [], secondary: [], settings: [] };
    const done = !!(opts && opts.setupDone);
    (rail || RAIL).forEach(function (r) {
      if (r.id === "setup" && done) return;
      if (out[r.group]) out[r.group].push(r);
    });
    return out;
  }
  const HOME_HIDDEN = ["bridge", "brain"];       // the Core's layout.HIDDEN_AT_FIRST: the top bar says both

  /* ---- lane I10a: the PuffPAI store (GET /v1/packages/available, apps and pages only) ---- */
  const STORE_KINDS = ["app", "page"];
  const STORE_MAX_HOME = 6;
  const STORE_TILE = "store";
  const STORE_LINE = "Anyone can build an app that works on BuildBot.";
  /* Lane I18c: the developer examples that ship with PuffPAI. Never offered in the store or on Home; the
   * Developer page says how to add one. */
  const DEV_EXAMPLES = ["example-page"];
  const devExample = function (p) { return DEV_EXAMPLES.indexOf(p.id) >= 0; };
  function firstLine(text) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    const m = t.match(/^.*?[.!?](\s|$)/);
    return (m ? m[0] : t).trim();
  }
  function storeBy(p) {
    return [p.author ? "By " + p.author : "", p.version ? "version " + p.version : ""].filter(Boolean).join(", ");
  }
  function storeBuiltIn(p) {
    const icon = typeof p.icon === "string" && /^data:image\/(svg\+xml|png|webp|jpeg);base64,[A-Za-z0-9+\/=]+$/.test(p.icon) ? p.icon : null;
    const by = storeBy(p);
    return { id: p.id, name: String(p.name || p.id), kind: p.kind, icon: icon, line: firstLine(p.description),
             about: String(p.description || "").trim() + (by ? " " + by + "." : "") };
  }
  function storeApps(list) {
    return (Array.isArray(list) ? list : []).filter(function (p) {
      return p && typeof p.id === "string" && STORE_KINDS.indexOf(p.kind) >= 0 && !devExample(p);
    }).map(storeBuiltIn);
  }
  /* D96: one row of the store's index (GET /v1/store). Its icon is an https address and is never used: loading it
   * would tell GitHub with no consent, so a store row always shows its kind's glyph. Only a row the Core says is
   * verified from the official store reads as Verified. */
  const STORE_TIER = { verified: "Verified: PuffPAI read it at that exact commit.", community: "Community: PuffPAI has not read it." };
  function storeIndexed(r) {
    const tier = r.tier === "verified" && r.official === true ? "verified" : "community";
    return { id: r.id, add: "store:" + r.id, store: true, name: String(r.name || r.id), kind: r.kind, icon: null,
             glyph: storeGlyph(r.kind), line: firstLine(r.description), about: String(r.description || "").trim(),
             by: storeBy(r), tier: tier, tierLine: STORE_TIER[tier], official: r.official === true,
             from: r.official === true ? "" : "From " + String(r.source || "another source") + ".",
             pulled: r.yanked === true ? "Not offered. The store pulled it: " + String(r.yanked_why || "no reason given") : "" };
  }
  /* D96: the whole store panel from the built-in packages (GET /v1/packages/available) and the store's answer
   * (GET /v1/store; null when it has none). ONE row per id, and when an id ships built in the built-in row wins.
   * Left out: a later source's duplicate (not `used`), what is installed, and a second copy of a built-in look.
   * `lines`: each source that is out of date or could not be reached, in its own words. */
  function storeShelf(available, store) {
    const list = Array.isArray(available) ? available : [];
    const answer = store && typeof store === "object" ? store : {};
    const have = Object.create(null);            // an id like "constructor" is only an id here
    list.forEach(function (p) { if (p && typeof p.id === "string") have[p.id] = true; });
    const rows = list.filter(function (p) {
      return p && typeof p.id === "string" && ["ui", "wizard"].indexOf(p.kind) < 0 && !devExample(p);
    }).map(function (p) { return Object.assign(storeBuiltIn(p), { kind: String(p.kind || ""), glyph: storeGlyph(p.kind) }); });
    (Array.isArray(answer.packages) ? answer.packages : []).forEach(function (r) {
      if (!r || typeof r.id !== "string" || typeof r.kind !== "string" || have[r.id]) return;
      if (r.used !== true || r.installed === true || r.builtin_look === true || devExample(r)) return;
      have[r.id] = true;
      rows.push(storeIndexed(r));
    });
    const sources = (Array.isArray(answer.sources) ? answer.sources : []).filter(function (s) { return s && typeof s.source === "string"; });
    return { rows: rows, sections: storeSections(rows), sources: sources,
             count: rows.filter(function (p) { return !p.pulled; }).length,
             lines: sources.filter(function (s) { return s.stale === true || s.ok === false; })
               .map(function (s) { return String(s.line || ""); }).filter(Boolean) };
  }
  /* Home's one question after setup: "Add apps from the PuffPAI store?" Gone once she said Not now
   * (the "store" tile is hidden in the layout file) or when no store app is left. *saved*: the saved copy of the
   * store's index (GET /v1/store?saved=1, D96): Home is drawn without her asking, so it never reads the network,
   * and it offers only the official store's rows. */
  function storePrompt(settings, layout, list, saved) {
    const hidden = (((layout || {}).tiles || {}).hidden) || [];
    const apps = storeApps(list).concat(storeShelf(list, saved).rows.filter(function (p) {
      return p.store && p.official && !p.pulled && STORE_KINDS.indexOf(p.kind) >= 0;
    }));
    const show = !!(settings && settings.wizard_complete) && hidden.indexOf(STORE_TILE) < 0 && apps.length > 0;
    return { show: show, apps: show ? apps.slice(0, STORE_MAX_HOME) : [] };
  }
  /* The tiles part of the layout with the store question hidden (PUT /v1/settings/layout). */
  function storeNotNow(layout) {
    const t = Object.assign({ order: [], hidden: [], pinned: [], names: {} }, (layout || {}).tiles || {});
    return { tiles: Object.assign({}, t, { hidden: t.hidden.filter(function (h) { return h !== STORE_TILE; }).concat([STORE_TILE]) }) };
  }

  /* ---- lane I10a: the bot's name and theme (GET /v1/settings `bot`, GET /v1/settings/themes) ---- */
  const BOT_NAME = "Puff";
  function botName(settings) {
    const n = settings && settings.bot && typeof settings.bot.name === "string" ? settings.bot.name.replace(/\s+/g, " ").trim() : "";
    return n ? n.slice(0, 40) : BOT_NAME;
  }
  function greetingFor(hour) {
    const h = Number(hour);
    return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  }
  function helloLine(hour, settings) { return greetingFor(hour) + ", I'm " + botName(settings) + "."; }
  /* The palettes the Desktop ships with (desktop.css, day and night): the theme when the Core has no list. */
  const OWN_THEMES = [{ id: "puffpai", name: "PuffPAI", mode: "both", tokens: {} }];
  const THEME_MODES = { night: "For night", day: "For day", both: "Day and night" };
  /* A theme's tokens, kept only when safe as a CSS variable: a --name, and a value with no url(),
   * no @, no braces, semicolons or angle brackets (nothing loaded from anywhere, nothing but a value).
   * Lane looks (D76): the same rule as the Core's (puffpai_core/looks.py): only letters, digits, spaces and
   * # % . , ( ) " ' + * / - _, and only the CSS functions in LOOK_FUNCTIONS (none of them loads anything). */
  const LOOK_FUNCTIONS = ["rgb", "rgba", "hsl", "hsla", "color-mix", "linear-gradient", "radial-gradient",
                          "calc", "var", "min", "max", "clamp"];
  function themeValueOk(s) {
    if (!s || s.length > 120 || /url\(|@|[{};<>\\]|expression|image-set|import/i.test(s)) return false;
    if (!/^[A-Za-z0-9 #%.,()"'+*/_-]+$/.test(s)) return false;
    const calls = s.match(/[A-Za-z-]*\s*\(/g) || [];
    return calls.every(function (c) { return LOOK_FUNCTIONS.indexOf(c.replace(/[\s(]/g, "").toLowerCase()) >= 0; });
  }
  function themeTokens(theme) {
    const out = {};
    const t = theme && theme.tokens && typeof theme.tokens === "object" ? theme.tokens : {};
    Object.keys(t).slice(0, 80).forEach(function (k) {
      const v = t[k];
      if (!/^--[A-Za-z0-9-]{1,40}$/.test(k) || typeof v !== "string" && typeof v !== "number") return;
      const s = String(v).trim();
      if (!themeValueOk(s)) return;
      out[k] = s;
    });
    return out;
  }
  function themeList(data) {
    const list = (Array.isArray(data) ? data : []).filter(function (t) { return t && typeof t.id === "string" && t.id; })
      .map(function (t) {
        const mode = THEME_MODES[t.mode] ? t.mode : "both";
        return { id: t.id, name: String(t.name || t.id), mode: mode, modeWords: THEME_MODES[mode], tokens: themeTokens(t),
                 dayTokens: themeTokens({ tokens: t.day_tokens }), line: typeof t.line === "string" ? t.line : "" };
      });
    return list.length ? list : OWN_THEMES.map(function (t) { return Object.assign({ modeWords: THEME_MODES[t.mode], dayTokens: {}, line: "" }, t); });
  }
  /* The tokens to set now. A "both" theme follows day and night: `tokens` at night, `dayTokens` by day
   * (lane I10c's day_tokens). A "night" or "day" theme sets its tokens whatever the hour. */
  function themeTokensFor(theme, part) {
    if (!theme || typeof theme !== "object") return {};
    const pick = theme.mode === "both" && part === "day" ? theme.dayTokens : theme.tokens;
    return pick && typeof pick === "object" ? pick : {};
  }
  function themeById(list, id) {
    return (list || []).find(function (t) { return t.id === id; }) || (list || [])[0] || null;
  }

  /* A page v2 has no Core part for yet: one plain card, naming what is missing. */
  /* D92: a signed-out Desktop shows one sign-in screen and nothing else. *where* is GET /v1/signin: the file
   * install.ps1 writes (Sign in a browser.cmd) and its folder, or empty when there is none. Plain words only:
   * never the Core's own refusal text. *why* is "expired" after a used or old link, else empty. *other*: the words
   * for signing in another browser on this computer (Settings). */
  const SIGN_IN_TITLE = "Sign in this browser";
  /* How to chat in a terminal, true to this install (GET /v1/signin). Bare "puffpai" is on no PATH (D92(e)). */
  function chatWords(where) {
    const w = where && typeof where === "object" ? where : {};
    const file = typeof w.chat_file === "string" ? w.chat_file.trim() : "", folder = typeof w.chat_folder === "string" ? w.chat_folder.trim() : "";
    const command = typeof w.chat_command === "string" ? w.chat_command.trim() : "";
    if (file && folder) return "To chat in a terminal: double-click " + file + " in " + folder + ". It is the same bot and the same main chat.";
    if (command) return "To chat in a terminal, run this there: " + command + ". It is the same bot and the same main chat.";
    return "To chat in a terminal: double-click Chat with BuildBot.cmd in the folder BuildBot is installed in. It is the same bot and the same main chat.";
  }
  function signInWords(where, why, other) {
    const w = where && typeof where === "object" ? where : {};
    const file = typeof w.file === "string" ? w.file.trim() : "", folder = typeof w.folder === "string" ? w.folder.trim() : "";
    const command = typeof w.command === "string" ? w.command.trim() : "";
    const who = other ? "another browser on this computer" : "this browser", there = other ? " in that browser" : "";
    const how = file && folder
      ? "To sign in " + who + ", double-click " + file + " in " + folder + ", then open the link it shows" + there + "."
      : command ? "To sign in " + who + ", run this in a terminal on this computer, then open the link it shows" + there + ": " + command
      : "To sign in " + who + ", double-click Sign in a browser.cmd in the folder BuildBot is installed in, then open the link it shows" + there + ".";
    const lead = why === "expired" ? "That sign-in link was already used, or is older than 10 minutes. " : "";
    if (typeof w.pair === "string" && w.pair) {     // D130: on the home network, a code from the bot's computer
      return { title: SIGN_IN_TITLE, line: lead + (other ? "To pair another device, get a code on the computer running your bot, then type it on that device." : "To sign in this device, type a pairing code from the computer running your bot.") };
    }
    return { title: SIGN_IN_TITLE, line: lead + how };
  }
  /* A Core answer that means "this browser is not signed in" (D92): the sign-in screen, never its words. Only the
   * Core's own sign-in refusal counts: a 401 with no code, or code "sign_in". A route's own 401 (a wrong vault
   * PIN answers code "wrong") is not a sign-out: it keeps its own words. */
  function signedOut(status, data) {
    if (status !== 401) return false;
    const code = data && typeof data === "object" ? data.code : undefined;
    return code === undefined || code === null || code === "" || code === "sign_in";
  }
  /* D92: Settings says what is not built yet in one plain line, never a nested box or a route name. */
  const NOT_BUILT = {
    brain: "Pick a model that is on this computer. The next chat turn uses it. To download another, open Models for each job.",
    tile: "This is not built yet.",
    vaultMove: "Moving or backing up the vault from here is not built yet.",
    botSave: "Not saved: saving the name and theme is not built yet.",
  };

  const LATER_TITLE = "Coming in a later build";
  function comingLater(missing, more) {
    return { title: LATER_TITLE, missing: String(missing || ""), line: "Not in this build: " + String(missing || "") + ".",
             more: more ? String(more) : "" };
  }

  /* A Toolsmith job's status (03 3.6, the Core's one set) in plain words. */
  const JOB_WORDS = {
    planned: "Planned", waiting_yes: "Waiting for your Yes", waiting_you: "Waiting for you, see Your turn",
    waiting_vault: "Waiting for your vault to be unlocked", held: "Held: Drawbridge set an app aside",
    building: "Building", working: "Working", not_yet: "Not working yet", nothing_found: "Nothing found yet",
    said_no: "You said No", no_answer: "No answer in time", free_site: "A free site does this, nothing installed",
  };
  /* D123: a job with no app, only some reading in the library. The Core says `library: true` on it (store.library_job);
   * its state stays `working`. Any other `working` job (an app, a free site) reads as it did. */
  const JOB_LIBRARY = "No app yet, your library has some reading on this";
  function jobGood(j) { return !!j && j.status === "working" && !j.library; }
  function jobWords(status, library) {
    const s = String(status || "planned");
    if (library && s === "working") return JOB_LIBRARY;
    if (s.indexOf("building: ") === 0) return "Trying " + s.slice(10);
    return JOB_WORDS[s] || s.replace(/_/g, " ");
  }
  const ACTIVE_JOBS = ["planned", "waiting_yes", "waiting_you", "waiting_vault", "building"];
  function jobActive(status) {
    const s = String(status || "");
    return s.indexOf("building") === 0 || ACTIVE_JOBS.indexOf(s) >= 0;
  }

  /* One build (GET /v1/toolsmith/builds/<id>) for the Building page: the plan, each job in plain words,
   * what it tries now, its last lines and result. "Try next app" only while a job is trying an app. */
  function buildView(b) {
    b = b || {};
    const jobs = (b.jobs || []).map(function (j) {
      const run = j.run || {};
      const status = j.status || run.state || "planned";
      const plan = j.plan || {};
      const cands = (plan.candidates || []).map(function (c) { return { name: String(c.name || c.full_name || ""), url: c.url || null }; });
      return {
        id: j.id, name: String(j.name || ""), does: String(j.does || ""), status: status, words: jobWords(status, j.library), library: j.library === true,
        active: jobActive(status), trying: run.trying || null, now: String(run.now || ""), since: String(run.since || ""), line: String(run.line || ""),
        lines: (run.lines || []).slice(-6).map(String), tries: (j.tries || []).map(String), candidates: cands,
        reuse: plan.reuse ? String(plan.reuse_name || plan.reuse) : "",
        tried: (run.tried || []).map(function (t) { return String((t && (t.name || t.ref)) || ""); }).filter(Boolean),
        result: run.result || null, canNext: status.indexOf("building") === 0 && !!run.trying,
      };
    });
    const card = b.card || {};
    const again = b.again && typeof b.again === "object" ? b.again : {};
    const active = !!b.planning || jobs.some(function (j) { return j.active; }) || storeProgress(b).adding;
    // Lane I18e: "Check again" on a started build that stopped with jobs held, not working yet or nothing found.
    const unfinished = jobs.filter(function (j) { return AGAIN_STATES.indexOf(j.status) >= 0; }).length;
    return {
      id: b.id, goal: String(b.goal || ""), created: String(b.created_at || ""), planning: !!b.planning, booksOnly: !!b.books_only,
      looked: b.looked && typeof b.looked === "object" ? { line: String(b.looked.line || ""), found: Number(b.looked.found) || 0 } : null,
      now: String(b.now || ""), nowAt: String(b.now_at || ""), said: String(b.said || (b.consent && b.consent.said) || ""),
      planLines: Array.isArray(card.lines) ? card.lines.map(String) : [],
      consentId: (b.consent && b.consent.id) || null, yes: !!b.yes,
      setup: (b.setup || []).map(function (s) { return String((s && s.title) || ""); }).filter(Boolean),
      knowledge: (b.knowledge || []).map(function (k) {
        return { title: String(k.title || ""), source: String(k.source || ""), kind: String(k.kind || ""),
                 look: String(k.look || ""), status: String(k.status || "") };
      }),
      cost: b.cost && b.cost.calls ? "$" + Number(b.cost.usd || 0).toFixed(2) + " over " + b.cost.calls + " cloud calls" : "",
      jobs: jobs, active: active,
      store: storeProgress(b),            // the ticked store apps, added by the Core before the build (lane I16)
      canAgain: !!b.yes && !active && unfinished > 0, unfinished: unfinished,
      againAsking: again.state === "asking" && unfinished > 0 ? String(again.consent_id || "") : "",
      unlock: buildKeys(b).unlock,        // D68: a PIN here lets it go on (lane I19a)
    };
  }

  /* What a plan is doing, in the words it deserves: a books-only plan looks for no app. */
  const BOOKS_CARD = "Get these for your library?";     // store.card's title for a plan of books only
  function planningWords(v) {
    return v && v.booksOnly ? "Toolsmith is looking through the libraries for books." : "Toolsmith is planning: finding apps for each job.";
  }
  function buildTitle(goal, booksOnly) {
    const g = String(goal || "");
    if (!booksOnly) return g || "A build";
    return /^books\b/i.test(g) ? g : "Books: " + g;
  }
  /* Find books while it looks: what the build says now, with how long it has been. Under a minute D113's line says
   * nothing, so the seconds are said here. */
  function findLive(what, since, startedMs, nowMs) {
    const line = String(what || "").trim().replace(/[. ]+$/, "");
    const s = Math.max(0, Math.floor((nowMs - startedMs) / 1000));
    return stillWords(line, since || new Date(startedMs).toISOString(), nowMs) || line + ", " + s + (s === 1 ? " second" : " seconds") + " so far.";
  }

  /* D68 (lane I19a): a build's keys, from the Core's build view (GET /v1/toolsmith/builds/<id>, "keys").
   * unlock: the build needs its cloud key, holds none, and the vault is locked now, so her Yes (or "Check
   * again", or a build waiting for the vault) asks for her PIN in the same step. */
  function buildKeys(b) {
    const k = b && b.keys && typeof b.keys === "object" ? b.keys : {};
    return { cloud: k.cloud === true, leased: k.leased === true, unlock: k.unlock === true };
  }
  const UNLOCK_FOR_YES = "Your vault is locked. Type your PIN: your Yes opens it, and Toolsmith keeps the key it needs until this build ends.";
  const UNLOCK_WAITING = "Toolsmith is waiting for your vault. Type your PIN to let it go on.";
  const UNLOCK_TYPE_PIN = "Type your PIN first. Nothing was answered yet.";

  /* D113: a build that waits for a locked vault says so where she is, once. The id of the first build with a job
   * waiting for it (a row of GET /v1/toolsmith, or a build view), else "". */
  const VAULT_WAIT = "Your vault is locked, so Toolsmith is waiting.";
  const VAULT_WAIT_HOME = "Open Building to type your PIN.";
  const VAULT_WAIT_WINDOW = VAULT_WAIT + " Building is open beside this chat: type your PIN there.";
  function vaultWaiting(status) {
    const b = ((status && status.builds) || []).find(function (x) {
      return (x.jobs || []).some(function (j) { return j.status === "waiting_vault"; });
    });
    return b ? String(b.id || "") : "";
  }

  /* D113: a long step's sign of life, from what is already there: what it does now and since when. "" under a
   * minute, or when "since" can't be read. */
  function stillWords(what, since, nowMs) {
    const at = Date.parse(String(since || ""));
    const n = Math.floor((nowMs - at) / 60000);
    if (!(n >= 1)) return "";
    return "Still working: " + String(what).trim().replace(/[. ]+$/, "") + ", " + n + (n === 1 ? " minute" : " minutes") + " so far.";
  }
  /* One job (a job of buildView, or of the Setup page's alive). Only while it builds: a job that waits is not working.
   * *named*: where the job's name is not already above the line. */
  function stillWorking(j, nowMs, named) {
    const status = String(j.status || "");
    if (status.indexOf("building") !== 0) return "";
    const what = String(j.now || j.trying || status.slice(10) || "").trim();
    const name = String(j.name || "");
    return stillWords(named || !what ? name + (what ? ": " + what : "") : what, j.since, nowMs);
  }
  /* The Setup page's one place for how things stand now (the "alive" of a build screen). */
  function setupAlive(alive, nowMs) {
    const a = alive || {};
    return { vault: a.vault === true, lines: (a.jobs || []).map(function (j) { return stillWorking(j, nowMs, true); }).filter(Boolean) };
  }

  /* D113: a poll that got no answer tries again, at its usual pace, and says so. A plain refusal does not. */
  const POLL_RETRY = "BuildBot did not answer just now, so this may be out of date. Trying again.";
  const POLL_WAIT_MS = 30000;                // a poll with no answer by then counts as failed
  function pollCanRetry(status) { return status === 0 || (status >= 500 && status !== 501); }

  /* Home's "Building now" tile from GET /v1/toolsmith: the builds still going, newest first. */
  function buildingNow(status) {
    const builds = ((status && status.builds) || []).slice().sort(function (a, b) {
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });
    const live = builds.filter(function (b) { return (b.jobs || []).some(function (j) { return jobActive(j.status); }); });
    return { where: status && status.where === "cloud" ? "Toolsmith uses " + (status.company || status.provider || "a cloud model")
                                                          : "Toolsmith uses this computer's model",
             live: live.map(function (b) {
               return { id: b.id, goal: String(b.goal || ""), jobs: (b.jobs || []).map(function (j) {
                 return { name: String(j.name || ""), words: jobWords(j.status, j.library) };
               }) };
             }),
             total: builds.length };
  }

  /* Lane I18c: the newest build, when it stopped with jobs that did not get an app (held, not working yet,
   * nothing found) and nothing in it is still going. From GET /v1/toolsmith (each job's status), so Home and
   * Building say so plainly instead of "not building anything". Null when there is nothing to say. */
  const STOPPED_KINDS = [["held", "held"], ["not_yet", "not working yet"], ["nothing_found", "with nothing found yet"]];
  const AGAIN_STATES = STOPPED_KINDS.map(function (k) { return k[0]; });
  function buildStopped(status) {
    const b = ((status && status.builds) || []).slice().sort(function (x, y) {
      return String(y.created_at || "").localeCompare(String(x.created_at || ""));
    })[0];
    const jobs = (b && b.jobs) || [];
    if (!jobs.length || jobs.some(function (j) { return jobActive(j.status); })) return null;
    const parts = [];
    let n = 0;
    STOPPED_KINDS.forEach(function (k) {
      const c = jobs.filter(function (j) { return j.status === k[0]; }).length;
      if (!c) return;
      n += c;
      parts.push(c + (c === 1 ? " job " : " jobs ") + k[1]);
    });
    if (!n) return null;
    const list = parts.length === 1 ? parts[0] : parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1];
    const reading = jobs.filter(function (j) { return j.library === true; }).length;
    const head = "Your last build stopped with " + list + "."
      + (reading ? " " + reading + (reading === 1 ? " job has no app yet, your library has some reading on it." : " jobs have no app yet, your library has some reading on them.") : "");
    return { id: b.id, goal: String(b.goal || ""), count: n, head: head, line: head + " Open Building to see why.",
             here: head + " Each job below says why.",
             // lane I18e: Home links to this build's "Check again" on Building
             link: "#building/" + encodeURIComponent(b.id), linkText: "Open Building to see why and check again." };
  }

  /* D123: the newest build, when it ended with nothing stopped and some jobs have no app, only some reading in the
   * library. Home and Building say so, in place of "not building anything". Null when there is nothing to say. */
  function buildReading(status) {
    const b = ((status && status.builds) || []).slice().sort(function (x, y) {
      return String(y.created_at || "").localeCompare(String(x.created_at || ""));
    })[0];
    const jobs = (b && b.jobs) || [];
    const n = jobs.filter(function (j) { return j.library === true; }).length;
    if (!n || jobs.some(function (j) { return jobActive(j.status) || AGAIN_STATES.indexOf(j.status) >= 0; })) return null;
    const head = n === jobs.length ? "Your last build has no apps yet. Your library has some reading on this."
      : n === 1 ? "1 job of your last build has no app yet. Your library has some reading on it."
      : n + " jobs of your last build have no app yet. Your library has some reading on them.";
    return { id: b.id, goal: String(b.goal || ""), count: n, head: head,
             next: "To look for an app, ask the chat, or paste a GitHub address in Apps.",
             link: "#building/" + encodeURIComponent(b.id), linkText: "Open Building to see each job." };
  }
  /* D123: said after "Jobs that work stay as they are": Check again leaves a job with only reading alone too. */
  function againMore(v) {
    return v.jobs.some(function (j) { return j.library; }) ? " A job with only reading from your library stays as it is too." : "";
  }

  /* What's running, from the Registry rows (Caretaker's health on each). */
  function runningSummary(rows) {
    const out = { running: 0, ready: 0, stopped: 0, broken: 0, needs: 0, down: [] };
    (rows || []).forEach(function (r) {
      if (r.status === "removed") return;
      if (r.status === "running") out.running++;
      else if (r.status === "broken") out.broken++;
      else if (r.status === "needs_you") out.needs++;
      else if (r.status === "stopped" && r.process === false) out.ready++;
      else out.stopped++;
      if (r.status === "running" && !(r.health && r.health.ok)) out.down.push(String(r.name || r.id));
    });
    return out;
  }

  /* The brain (GET /v1/model) in one line. */
  const BRAIN_STATE = { ready: "Ready", loading: "Loading", not_downloaded: "Not downloaded yet", none: "Pick one in Setup",
                        timed_out: "Took too long to answer", error: "Not working" };
  function brainView(m) {
    const s = (m && m.settings) || {};
    const st = (m && m.state) || {};
    const name = s.model || "";
    const state = name ? String(st.state || "") : "none";
    return { name: name || "No brain picked yet", state: state, words: BRAIN_STATE[state] || (state ? state.replace(/_/g, " ") : "Not checked"),
             reason: name && st.reason ? String(st.reason) : "", ok: state === "ready", address: String(s.address || ""),
             context: s.effective && s.effective.context ? s.effective.context : null };
  }

  /* One line of the Bridge's outside log (GET /v1/bridge/ledger) in plain words: who, which host,
   * allowed or refused, and why. *names*: {package id: name}. */
  const WHY = {
    bridge_closed: "the Bridge was closed", switched_off: "that switch is off", not_on_list: "that host is not on its list",
    blocked_by_provider: "a block list said no", labelled: "it carried a private detail", needs_yes: "it needed your Yes",
    granted: "you said Yes to it", inside_address: "it pointed inside your network", network_filter: "a network filter said no",
    cannot_look_up: "the name could not be looked up", no_answer: "nothing answered", not_encrypted: "it was not encrypted",
    not_allowed: "it was not allowed", bad_answer: "the answer was broken", too_big: "it was too big",
    bad_credential: "a sign-in was wrong", lan_only: "it may only reach your home network", around_the_bridge: "it tried to go around the Bridge",
    drawbridge_down: "Drawbridge was not running", policy_damaged: "the Bridge's settings were damaged", probe: "a check by BuildBot",
  };
  function whoOf(subject, names) {
    const s = String(subject || "");
    names = names || {};
    if (s.indexOf("pkg:") === 0) { const id = s.slice(4); return names[id] || id; }
    if (s === "toolsmith:search") return "Toolsmith, searching for apps";
    if (s === "toolsmith:cloud") return "Toolsmith's cloud model";
    if (s === "toolsmith:read") return "Toolsmith, reading a public web page";
    if (s.indexOf("agent:") === 0) { const a = s.slice(6); return a.charAt(0).toUpperCase() + a.slice(1); }
    if (s.indexOf("core:") === 0) return "BuildBot (" + s.slice(5) + ")";
    if (s.indexOf("provider:") === 0) { const id = s.slice(9); return names[id] || id; }
    return "Something unknown";
  }
  function ledgerLine(rec, names) {
    rec = rec || {};
    if (rec.event === "chain_restarted") return { when: rec.t || "", text: "The outside log started again.", allowed: null };
    if (rec.event === "chain_rotated") return { when: rec.t || "", text: "The outside log started a new file.", allowed: null };
    const who = whoOf(rec.subject, names);
    const host = rec.host ? String(rec.host) : "a host it did not name";
    const allowed = rec.outcome === "done" || rec.outcome === "failed";
    const why = WHY[rec.code] || "";
    let text;
    if (rec.outcome === "done") text = who + " reached " + host + (why ? ": " + why + "." : ".");
    else if (rec.outcome === "failed") text = who + " was allowed to reach " + host + ", but it did not work" + (why ? ": " + why + "." : ".");
    else text = who + " was refused " + host + (why ? ": " + why + "." : ".");
    return { when: String(rec.t || ""), who: who, host: host, allowed: allowed, outcome: rec.outcome, why: why, text: text };
  }

  /* One audit line (GET /v1/audit) in plain words; Drawbridge's checks are the ones the Agent Team shows. */
  function auditLine(line) {
    line = line || {};
    const v = String(line.verdict || "");
    if (line.actor === "drawbridge" && line.action === "check_package")
      return { when: String(line.t || ""), drawbridge: true, text: "Drawbridge checked " + (line.package || "a package") + ": " + (v || "no verdict") + "." };
    if (line.actor === "owner" && line.action === "drawbridge_files")         // D95: her setting, the two names only
      return { when: String(line.t || ""), drawbridge: true,
               text: "You set files Drawbridge can't read to " + (line.to === "locked" ? "Locked down" : line.to === "normal" ? "Normal" : "an unknown setting") + "." };
    if (line.actor === "drawbridge" && line.action === "review")
      return { when: String(line.t || ""), drawbridge: true,
               text: "Drawbridge reviewed " + (line.package || "?") + "." + (line.tool || "?") + ": " + (v || "no verdict") + (line.code ? " (" + line.code + ")" : "") + "." };
    const what = [line.actor, line.action || line.event].filter(Boolean).join(" ");
    return { when: String(line.t || ""), drawbridge: false, text: (what || "A line") + (line.package ? " " + line.package : "") + (v ? ": " + v : "") + "." };
  }
  function drawbridgeChecks(lines, n) {
    return (lines || []).map(auditLine).filter(function (l) { return l.drawbridge; }).reverse().slice(0, n || 5);
  }

  /* Home tiles in her order (v1 layout): pinned first, then her order, then new ones; hidden go to the tray.
   * *tiles*: [{id, name}]. *layout*: GET /v1/settings/layout. */
  function arrangeTiles(tiles, layout) {
    const t = (layout && layout.tiles) || {};
    const names = t.names || {};
    const hidden = t.hidden || [];
    const byId = {};
    (tiles || []).forEach(function (x) { byId[x.id] = x; });
    const seq = [];
    (t.pinned || []).concat(t.order || []).concat((tiles || []).map(function (x) { return x.id; })).forEach(function (id) {
      if (byId[id] && seq.indexOf(id) < 0) seq.push(id);
    });
    const shown = function (id) { return Object.assign({}, byId[id], { name: names[id] || byId[id].name, pinned: (t.pinned || []).indexOf(id) >= 0 }); };
    return { visible: seq.filter(function (id) { return hidden.indexOf(id) < 0; }).map(shown),
             hidden: seq.filter(function (id) { return hidden.indexOf(id) >= 0; }).map(shown) };
  }
  /* "Move up" or "move down" as the arrange route's 1-based position. Null at an end. */
  function movePosition(visibleIds, id, delta) {
    const i = (visibleIds || []).indexOf(id);
    const j = i + delta;
    if (i < 0 || j < 0 || j >= visibleIds.length) return null;
    return j + 1;
  }
  function seenTiles(tiles) {
    const out = {};
    (tiles || []).forEach(function (t) { out[t.id] = String(t.name || t.id).slice(0, 80); });
    return out;
  }

  /* D82 (PROPOSED): Home shows the installed apps by itself. homeApps: one launcher per package that has a page
   * or opens a program (the tray's first item for it), each opening the D13 way. */
  const APP_TILE = "app/";
  function homeApps(rows, base) {
    const seen = {};
    return trayItems(rows, base).filter(function (i) {
      if (seen[i.pkg] || i.target.kind === "none") return false;
      seen[i.pkg] = true;
      return true;
    }).map(function (i) {
      const row = (rows || []).find(function (r) { return r.id === i.pkg; }) || {};
      return Object.assign({}, i, { name: row.name || i.label, page: !!((row.ui || {}).pages || []).length,
                                    running: row.status === "running" });
    });
  }
  /* Which children stay put when Home is put back in order: the heaviest run already in the right order
   * (a framed tile weighs most, since moving it reloads the app inside). *order*: each current child's place
   * in the new order; *weight*: each one's weight. Returns the current indexes that stay. */
  function stayers(order, weight) {
    const best = [], prev = [];
    let k = -1;
    for (let i = 0; i < order.length; i++) {
      best[i] = weight[i]; prev[i] = -1;
      for (let j = 0; j < i; j++) if (order[j] < order[i] && best[j] + weight[i] > best[i]) { best[i] = best[j] + weight[i]; prev[i] = j; }
      if (k < 0 || best[i] > best[k]) k = i;
    }
    const keep = [];
    for (; k >= 0; k = prev[k]) keep.unshift(k);
    return keep;
  }
  /* The package ids whose own page has a Home tile, app/<id>: every app with a page when apps show as
   * pages, else the ones she put there (newest first). Removed or page-less ones never. */
  function appPageIds(apps, layout) {
    const a = (layout && layout.apps) || {};
    const withPage = (apps || []).filter(function (x) { return x.page; }).map(function (x) { return x.pkg; });
    const hidden = ((layout && layout.tiles) || {}).hidden || [];         // taken off Home in "pages" view
    const shown = withPage.filter(function (id) { return hidden.indexOf(APP_TILE + id) < 0; });
    const picked = (a.pages || []).filter(function (id) { return shown.indexOf(id) >= 0; });
    if (a.view !== "pages") return picked;
    return picked.concat(shown.filter(function (id) { return picked.indexOf(id) < 0; }));
  }
  /* The last check before a frame (the Core's GET /v1/packages/<id>/frame decided first). D83: only a
   * package's own origin for the pages address *base* (D53, http://<id>.localhost:8422), with no sign-in
   * in it, and never the Desktop's own origin *here*. Outside apps (127.0.0.1, localhost, anything else)
   * are never framed: they open in her browser. */
  function frameSrcOk(url, here, base) {
    let u;
    try { u = new URL(String(url || "")); } catch (e) { return false; }
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (u.username || u.password) return false;
    if (isNetwork(base)) {       // D130: exactly one package's own port, never the Desktop's own origin
      if (!Object.keys(base.ports).some(function (id) { return pkgOrigin(id, base) === u.origin; })) return false;
      try { return !!here && u.origin !== new URL(here).origin; } catch (e) { return false; }
    }
    const m = /^([a-z][a-z0-9-]{1,39})\.localhost$/.exec(u.hostname.toLowerCase());
    if (!m || u.origin !== pkgOrigin(m[1], base)) return false;
    try { if (!here || u.origin === new URL(here).origin) return false; } catch (e) { return false; }
    return true;
  }
  /* A frame's sandbox: only these rights, never top navigation (the app must not move the Desktop away). */
  const FRAME_RIGHTS = ["allow-scripts", "allow-forms", "allow-popups", "allow-same-origin", "allow-downloads"];
  function frameSandbox(s) {
    const out = [];
    String(s || "").split(/\s+/).forEach(function (r) { if (FRAME_RIGHTS.indexOf(r) >= 0 && out.indexOf(r) < 0) out.push(r); });
    return out.join(" ");
  }

  /* Words that plainly ask for books or reading, not an app: what they are about, or "". "books about lighthouses",
   * "a book on tides", "find me novels by Jane Austen", "the book Moby Dick", "something to read about whales". A book
   * tracker, a recipe book for my family, or an app for my books is an app. */
  const BOOKS_ASK = new RegExp("^(?:please\\s+)?(?:(?:find|get|add|show|download|fetch)(?:\\s+me)?\\s+|i\\s+(?:want|need|would\\s+like)\\s+|look(?:ing)?\\s+for\\s+)?"
    + "(?:(?:a|an|some|the|any|more|free|old|public\\s+domain)\\s+)*"
    + "(?:(?:e-?)?books?|novels?|something\\s+to\\s+read|reading|reference\\s+(?:books?|articles?)|articles?)\\s+(?:about|on|by|called|titled|named)\\s+(.+)$", "i");
  const BOOK_TITLED = /^the\s+book\s+(.+)$/i;
  const AN_APP_WORD = /\b(apps?|programs?|software|tools?|trackers?|managers?|organi[sz]ers?|clubs?)\b/i;
  function booksWords(text) {
    const t = String(text || "").trim().replace(/[.?!]+$/, "");
    const m = BOOKS_ASK.exec(t) || BOOK_TITLED.exec(t);
    return m && !AN_APP_WORD.test(t) ? m[1].trim() : "";
  }

  /* What she typed in Apps > Add: a GitHub project (Toolsmith tries only that one), a folder on this
   * computer (the Package Manager adds it), or words (Toolsmith plans from them). */
  const GITHUB = /^https?:\/\/(www\.)?github\.com\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+?)(\.git)?\/?(#.*)?$/;
  function addSource(text) {
    const t = String(text || "").trim();
    if (!t) return { kind: "empty" };
    const g = GITHUB.exec(t);
    if (g) {
      const repo = g[3];
      return { kind: "github", url: "https://github.com/" + g[2] + "/" + repo,
               plan: { goal: "Add " + repo + " from GitHub",
                       jobs: [{ name: repo, does: "Run " + repo + ", the project at https://github.com/" + g[2] + "/" + repo,
                                pasted: ["https://github.com/" + g[2] + "/" + repo] }] } };
    }
    if (/^([A-Za-z]:[\\/]|\\\\|\/|~[\\/])/.test(t)) return { kind: "folder", source: t };
    const words = booksWords(t);
    if (words) return { kind: "books", words: words };
    if (/^[a-z][a-z0-9-]{1,39}$/.test(t)) return { kind: "builtin", source: t };
    return { kind: "describe", plan: { goal: t.slice(0, 600) } };
  }

  /* Available apps: the built-in packages not installed yet (GET /v1/packages/available), less the ones
   * that are not apps for her to pick (the Desktop and wizard kinds). */
  function availableApps(list) {
    return (list || []).filter(function (p) { return p && ["ui", "wizard"].indexOf(p.kind) < 0 && !devExample(p); }).map(function (p) {
      return { id: p.id, name: String(p.name || p.id), kind: String(p.kind || ""), description: String(p.description || "") };
    });
  }

  /* The sections of the one store panel: Apps, Looks, Avatars, Tools, Skills (lane looks, D96). Any other kind
   * goes under Other parts, with its kind said on its row: the list of kinds grows, and none is assumed here. */
  const STORE_SECTIONS = [
    { kinds: ["app", "page"], title: "Apps", glyph: "widgets", kindWord: true, line: "" },
    { kinds: ["look"], title: "Looks", glyph: "palette", kindWord: true, line: "A look changes colours, corners and fonts. Pick it on Looks once it is added." },
    { kinds: ["avatar"], title: "Avatars", glyph: "face", kindWord: true, line: "A face for your bot. Pick it on Looks once it is added." },
    { kinds: ["tool"], title: "Tools", glyph: "build", kindWord: true, line: "A tool is something new your bot's brain can do." },
    { kinds: ["skill"], title: "Skills", glyph: "school", kindWord: true, line: "A skill is written steps your bot's brain can follow." },
  ];
  const STORE_OTHER = { kinds: [], title: "Other parts", glyph: "extension", kindWord: false, line: "" };
  function storeGlyph(kind) {
    return (STORE_SECTIONS.find(function (s) { return s.kinds.indexOf(kind) >= 0; }) || STORE_OTHER).glyph;
  }
  function storeSections(parts) {
    const list = Array.isArray(parts) ? parts : [];
    const known = [].concat.apply([], STORE_SECTIONS.map(function (s) { return s.kinds; }));
    const out = STORE_SECTIONS.map(function (s) {
      return Object.assign({}, s, { items: list.filter(function (p) { return s.kinds.indexOf(p.kind) >= 0; }) });
    });
    out.push(Object.assign({}, STORE_OTHER, { items: list.filter(function (p) { return known.indexOf(p.kind) < 0; }) }));
    return out.filter(function (s) { return s.items.length; });
  }

  /* An installed row's buttons on the Apps page. Start and Stop only for a package with a process.
   * *waiting*: an update's Yes card waits for this app (updatesWaiting), so its button says Update. */
  function appActions(row, waiting) {
    const out = [];
    const proc = row.process !== false && ["ui", "avatar", "look"].indexOf(row.kind) < 0;
    if (proc && row.status !== "running") out.push({ id: "start", label: "Start", icon: "play_arrow" });
    if (proc && row.status === "running") out.push({ id: "stop", label: "Stop", icon: "stop" });
    out.push(waiting ? { id: "update", label: "Update", icon: "update", waiting: true }
                     : { id: "update", label: "Check for an update", icon: "update" });
    out.push({ id: "logs", label: "Logs", icon: "article" });
    out.push({ id: "remove", label: "Remove", icon: "delete" });
    return out;
  }

  /* The chat stream (POST /v1/chat/stream, NDJSON): split what came so far into whole lines. */
  function ndjson(buffer) {
    const parts = String(buffer || "").split("\n");
    const rest = parts.pop();
    const events = [];
    parts.forEach(function (p) {
      if (!p.trim()) return;
      try { events.push(JSON.parse(p)); } catch (e) { events.push({ type: "error", error: "The Core sent a line the Desktop can't read." }); }
    });
    return { events: events, rest: rest };
  }
  /* One bot turn in the conversation, from its stream events: the words so far, each tool step, each Yes
   * card asked for, links it opened, and the end. *talking* drives Puff's mouth. */
  /* D140: a fix the bot shows, with the passage it means. Only text, set as text. */
  function passageView(ev) {
    const s = function (v) { return typeof v === "string" ? v : ""; };
    const part = Number(ev.part) || 0, of = Number(ev.of) || 0;
    return { where: s(ev.document) + (part ? ", part " + part + (of ? " of " + of : "") : ""), before: s(ev.before), passage: s(ev.passage),
             after: s(ev.after), suggestion: s(ev.suggestion), why: s(ev.why) };
  }
  function ChatTurn() {
    this.text = ""; this.steps = []; this.asks = []; this.opens = []; this.done = false; this.error = ""; this.notice = "";
    this.changed = []; this.passages = [];
  }
  ChatTurn.prototype.add = function (ev) {
    if (!ev || typeof ev !== "object") return;
    if (ev.type === "token") this.text += String(ev.text || "");
    else if (ev.type === "progress") this.steps.push({ tool: String(ev.tool || ""), line: String(ev.line || ""), state: "running" });
    else if (ev.type === "tool_done") {
      const s = this.steps.filter(function (x) { return x.tool === String(ev.tool || "") && x.state === "running"; })[0];
      if (s) s.state = ev.ok ? "done" : "failed";
      else this.steps.push({ tool: String(ev.tool || ""), line: "", state: ev.ok ? "done" : "failed" });
    } else if (ev.type === "ask") {
      const q = ev.question || {};
      this.asks.push({ id: q.consent_id || q.id || null, title: (q.card && q.card.title) || q.title || "Your Yes is needed",
                       answered: null });
    } else if (ev.type === "answered") {
      const a = this.asks.filter(function (x) { return x.answered === null; })[0];
      if (a) a.answered = ev.yes === true;
    } else if (ev.type === "open") (ev.urls || []).forEach(function (u) { if (/^https?:\/\//.test(u)) this.opens.push(String(u)); }, this);
    else if (ev.type === "passage") this.passages.push(passageView(ev));
    else if (ev.type === "done") {
      this.done = true;
      if (typeof ev.text === "string" && ev.text.length >= this.text.length) this.text = ev.text;
      this.notice = ev.notice ? String(ev.notice) : "";
      this.changed = (ev.changed || []).map(function (c) { return String((c && c.tool) || c); });
    } else if (ev.type === "error") { this.done = true; this.error = String(ev.error || "The brain did not answer."); }
  };
  ChatTurn.prototype.talking = function () { return !this.done && this.text.length > 0; };
  const STEP_WORDS = { running: "Working", done: "Done", failed: "Did not work" };
  function stepWords(state) { return STEP_WORDS[state] || state; }

  /* The Developer terminal (lane I8c's route): its event stream, {t, stream, text} lines then {exit}. */
  function ShellLog(max) { this.lines = []; this.exit = null; this.max = max || 2000; }
  ShellLog.prototype.add = function (ev) {
    if (!ev || typeof ev !== "object") return;
    if (Object.prototype.hasOwnProperty.call(ev, "exit")) { this.exit = ev.exit; return; }
    if (typeof ev.text !== "string") return;
    // lane I8c names its error stream "err"; "stderr" is kept for the Desktop's own lines
    const s = ev.stream === "err" || ev.stream === "stderr" ? "stderr" : ev.stream === "stdin" ? "stdin" : "stdout";
    this.lines.push({ stream: s, text: ev.text });
    if (this.lines.length > this.max) this.lines.splice(0, this.lines.length - this.max);
  };
  const SHELL_NOTE = "This is a full command line on your computer. Commands here are not checked by the Bridge.";

  /* The seven agents (01, docs/09_AGENT_SECURITY.md section 1): zone, what each does, built in this build or
   * not, and its live line where a route gives one. *live*: what the page read. */
  const AGENTS = [
    { id: "archivist", name: "Archivist", zone: "Zone 1, Vault", does: "Holds every key and your vault. Lends a key for one use; never hands it over.", built: "Built: the vault, its PIN, recovery code and inbox." },
    { id: "concierge", name: "Concierge", zone: "Zone 2, Brain", does: "Runs each chat turn: picks the tools, asks for your Yes when a tool changes something.", built: "Built: chat, tool steps and Yes cards in chat." },
    { id: "steward", name: "Steward", zone: "Zone 2, Brain", does: "Starts and stops every app, each in its own box, on this computer only.", built: "Built: start, stop, logs, the after-start check." },
    { id: "caretaker", name: "Caretaker", zone: "Zone 2, Brain", does: "Checks each app's health, restarts one that failed, and puts BuildBot in safe mode when a check fails.", built: "Built: health checks and safe mode." },
    { id: "majordomo", name: "Majordomo", zone: "Zone 2, Brain", does: "Keeps your Your turn list and, later, your saved routines and reminders.", built: "Partly built: the Your turn list. Routines are not in this build." },
    { id: "drawbridge", name: "Drawbridge", zone: "Zone 3, Bridge", does: "Checks everything that comes in and every change a tool makes, by fixed rules. No answer means no.", built: "Built: package checks and tool reviews, written to the audit log." },
    { id: "toolsmith", name: "Toolsmith", zone: "Zone 3, Bridge", does: "Finds ready-made apps for what you want, tries them in a folder of their own, and proves they work.", built: "Built: plans, builds, tries, the one Yes." },
  ];
  function agentRows(live) {
    live = live || {};
    return AGENTS.map(function (a) {
      let now = "";
      if (a.id === "archivist" && live.vault) now = "Your vault is " + String(live.vault.state || "not checked").replace(/_/g, " ") + ".";
      if (a.id === "concierge" && live.brain) now = "The brain: " + live.brain.name + ", " + live.brain.words.toLowerCase() + ".";
      if (a.id === "steward" && live.running) now = live.running.running + " running, " + live.running.stopped + " stopped.";
      if (a.id === "caretaker" && live.running) now = live.running.broken ? live.running.broken + " not working." : (live.running.down.length ? "Not answering: " + live.running.down.join(", ") + "." : "Every running app answers its check.");
      if (a.id === "caretaker" && live.safe && live.safe.length) now = "Safe mode: " + live.safe.join(" ");
      if (a.id === "majordomo" && typeof live.steps === "number") now = live.steps ? live.steps + " things on Your turn." : "Nothing on Your turn.";
      if (a.id === "toolsmith" && live.building) now = live.building.live.length ? live.building.live.length + " build going now." : "Not building anything now. " + live.building.total + (live.building.total === 1 ? " build so far." : " builds so far.");
      if (a.id === "drawbridge" && live.checks) now = live.checks.length ? live.checks[0].text : "No checks in the audit log yet.";
      return Object.assign({}, a, { now: now });
    });
  }

  /* The vault (GET /v1/vault/status) in plain words. Never a value: there is none in the reply. */
  const VAULT_STATE = { open: "Open", locked: "Locked", not_set_up: "Not set up yet", missing: "Can't find its store",
                        throttled: "Locked: too many tries, wait a moment", safe_mode: "In safe mode" };
  function vaultView(s) {
    s = s || {};
    const state = String(s.state || "");
    return { state: state, words: VAULT_STATE[state] || (state ? state.replace(/_/g, " ") : "Not checked"),
             open: state === "open", setUp: state !== "not_set_up" && !!state,
             waysIn: (s.ways_in || []).map(String), items: typeof s.items === "number" ? s.items : null,
             inbox: Number(s.inbox_waiting || 0), conflicts: (s.inbox_conflicts || []).map(String),
             idle: s.idle_lock_minutes ? "Locks itself after " + s.idle_lock_minutes + " minutes with no use." : "",
             where: s.store && s.store.location ? String(s.store.location) : "", why: s.why ? String(s.why) : "",
             retry: Number(s.retry_after || 0) };
  }

  /* A new person's id from her name (the Core checks it: lower case letters, digits, _ or -). */
  function personId(name, taken) {
    let base = String(name || "").toLowerCase().normalize("NFKD");
    base = base.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 36) || "person";
    if (!/^[a-z0-9]/.test(base)) base = "p" + base;
    let id = base, n = 2;
    while ((taken || []).indexOf(id) >= 0) id = base + "-" + (n++);
    return id;
  }

  /* Zone provider slots (GET /v1/providers): one row each, plain. */
  function slotRows(list) {
    return (list || []).map(function (s) {
      const one = s.how_many === "one" || s.how_many === "zero_or_one";
      return { slot: String(s.slot), one: one, installed: (s.installed || []).map(String),
               active: one ? (s.active || null) : (s.active || []).map(String),
               canPick: one && (s.installed || []).length > 0 && s.slot !== "zone1.vault-store" };
    });
  }

  /* The face (lane I8b): the active avatar's `face` page on its package's own origin when it is
   * installed, else its preview picture there, else the Desktop's own copy of Puff, the owner's drawing
   * (/ui/img/puff.webp), so a fresh bot before setup never shows a broken picture. The face frame may
   * have allow-same-origin: its origin is its package's own (D53), and the Desktop's one message goes to
   * that origin only. */
  const FACE_SANDBOX = "allow-scripts allow-same-origin";
  const OWN_FACE = "/ui/img/puff.webp";
  function faceFor(avatar, rows, base) {
    const own = { kind: "own", src: OWN_FACE, name: (avatar && avatar.name) || "Puff", origin: null };
    if (!avatar || !avatar.id || !base) return own;
    const row = (rows || []).find((r) => r.id === avatar.id && r.status !== "removed");
    if (!row) return own;
    const page = ((row.ui || {}).pages || []).find((p) => p.id === "face");
    const t = page ? pageTarget(row, page, base) : null;
    if (t && t.kind === "desktop") return { kind: "face", src: t.src, name: avatar.name || row.name, origin: pkgOrigin(row.id, base) };
    const pic = onPages(avatar.preview, base);
    return pic ? { kind: "picture", src: pic, name: avatar.name || row.name, origin: null } : own;
  }

  /* The Developer command line's answers (lane I8c's /v1/dev/shell) in plain words. */
  const SHELL_MAX_LINE = 8000;
  function shellSaid(status, data) {
    const d = data || {};
    if (d.code === "safe_mode") return "BuildBot is in safe mode, so the command line is closed.";
    if (d.code === "too_many" || status === 429) return "Two command lines are already open. Close one first.";
    if (status === 403) return d.error || "The command line opens only for you at a paired device.";
    if (status === 404) return "That command line has closed.";
    if (status === 400) return d.error || "Send one line at a time, with no line breaks.";
    if (status === 503) return d.error || "The command line could not start.";
    return d.error || "That did not work.";
  }
  const CTRL_C_SAID = { stopped: "Stopped.", idle: "Nothing was running.",
                        restarted: "The command line was busy, so BuildBot started a fresh one." };
  function ctrlcSaid(result) { return CTRL_C_SAID[result] || "Sent Ctrl+C."; }
  /* The terminal's status line and whether it takes input. *t*: {id, cwd, shell, exited, closed, busy}.
   * A shell picked up again after a reload has no folder in the list, so only its shell name shows. */
  const SHELL_NAMES = { powershell: "PowerShell", cmd: "Command Prompt" };
  const SHELL_CLOSED = "This command line is closed.";
  function shellState(t) {
    t = t || {};
    if (!t.id) return { text: "No terminal open.", input: false, buttons: false };
    if (t.closed) return { text: SHELL_CLOSED, input: false, buttons: false };
    if (t.exited) return { text: "It ended.", input: false, buttons: false };
    const name = SHELL_NAMES[t.shell] || t.shell || "The command line";
    const text = t.cwd ? "Running in " + t.cwd : name + " is open.";
    return { text: t.busy ? text + " Stopping what runs..." : text, input: !t.busy, buttons: true };
  }
  /* One line for the route: no line breaks or NUL, at most 8000 characters; null when it can't be sent. */
  function shellLine(text) {
    const t = String(text == null ? "" : text);
    if (/[\r\n\u0000]/.test(t) || t.length > SHELL_MAX_LINE) return null;
    return t;
  }

  /* ================= lane I16: the pages for the Core's new parts ================= */

  /* Settings > Who your bot is (GET /v1/settings/persona). Its text has the Core's own sections (zone2
   * persona.render): "Role:", "What it can help with:", "How it talks:", "First questions it asks:" and
   * "What it won't do:". The owner's own words may have none of them; those lines are kept as they are. */
  function personaSections(text) {
    const out = { role: "", helps: [], talks: "", questions: [], wont: [], other: [] };
    let at = null;
    String(text || "").split(/\r?\n/).forEach(function (raw) {
      const line = raw.trim();
      if (!line) return;
      let m;
      if ((m = /^role\s*:\s*(.+)$/i.exec(line))) { out.role = m[1].replace(/\.$/, ""); at = null; return; }
      if (/^(knows(\s+well)?|what it can help with)\s*:?$/i.test(line)) { at = "helps"; return; }
      if ((m = /^how it talks\s*:\s*(.*)$/i.exec(line))) { out.talks = m[1]; at = null; return; }
      if (/^first questions/i.test(line)) { at = "questions"; return; }
      if (/^what it won.?t do\s*:?$/i.test(line)) { at = "wont"; return; }
      if (at === "questions" && (m = /^\d+[.)]\s+(.+)$/.exec(line))) { out.questions.push(m[1]); return; }
      if ((at === "helps" || at === "wont") && (m = /^[-*•]\s+(.+)$/.exec(line))) { out[at].push(m[1]); return; }
      at = null;
      out.other.push(line);
    });
    return out;
  }
  const PERSONA_NONE = "Nothing is written yet. Setup writes who your bot is from your goal, at the end of the plan step. " +
                       "You can also write it here yourself.";
  const PERSONA_REDO = "Writing it again from your goal, on this computer's brain. Nothing is sent. This can take a minute.";
  const PERSONA_DEFAULT = "The chat does not use this. It uses a plain role instead, because: ";
  function personaView(d) {
    d = d || {};
    const text = typeof d.text === "string" ? d.text : "";
    const state = d.in_use === "persona" || d.in_use === "plain default" ? d.in_use : (text ? "persona" : "none");
    const s = personaSections(text);
    if (typeof d.role === "string" && d.role) s.role = d.role;
    if (Array.isArray(d.knows) && d.knows.length) s.helps = d.knows.map(String);
    const flag = typeof d.flag === "string" ? d.flag : "";
    const built = String(d.built_with || "");
    const from = d.source === "owner" ? "In your own words."
      : d.source === "brain" ? "Written from your goal" + (built === "local" ? " on this computer's brain." : built ? " with " + built + "." : ".") : "";
    return Object.assign(s, { state: state, text: text, max: Number(d.max_chars) || 4000, flag: flag, from: from,
                              flagLine: state === "plain default" ? PERSONA_DEFAULT + (flag || "a check said no") : "" });
  }

  /* Settings > You (GET and PUT /v1/settings/you). The Core's errors start with the field's own name. */
  const YOU_LABELS = { name: "Your name", call_me: "What your bot calls you", about: "About you", timezone: "Time zone" };
  const YOU_LOCKED = "Unlock your vault to see this.";
  const YOU_INBOX = "Saved; it is filed when your vault opens.";
  function youError(text) {
    const t = String(text || "");
    const m = /^(call_me|name|about|timezone)\b\s*(.*)$/.exec(t);
    return m ? { field: m[1], text: YOU_LABELS[m[1]] + " " + m[2] } : { field: "", text: t };
  }
  function youFlags(d) {
    const f = (d && d.flag) || {};
    const line = function (k) { return typeof f[k] === "string" && f[k] ? "Not used in chat: " + f[k] : ""; };
    return { call_me: line("call_me"), about: line("about") };
  }
  function youSaved(d) { return d && d.kept === "inbox" ? YOU_INBOX : "Saved."; }
  /* The time zones to pick from: the browser's list (Intl), with the saved one and the browser's own in it.
   * The value is the saved one, else the browser's. */
  function timeZoneChoices(list, saved, browser) {
    const out = [];
    (Array.isArray(list) ? list : []).concat(["UTC", saved, browser]).forEach(function (z) {
      if (typeof z === "string" && z && out.indexOf(z) < 0) out.push(z);
    });
    out.sort();
    return { options: out, value: (typeof saved === "string" && saved) || (typeof browser === "string" && browser) || "" };
  }

  /* Looks: a theme's previews. A "both" theme shows night and day; the others their one part. */
  function swatchParts(theme) {
    if (!theme) return [];
    if (theme.mode === "both") {
      const out = [{ label: "Night", tokens: theme.tokens || {} }];
      if (theme.dayTokens && Object.keys(theme.dayTokens).length) out.push({ label: "Day", tokens: theme.dayTokens });
      return out;
    }
    return [{ label: theme.mode === "day" ? "Day" : "Night", tokens: theme.tokens || {} }];
  }

  /* Lane looks: repeated notices fold into one. *titles* are the notice cards' titles, newest first (the
   * order on the page). The newest of each title shows, with how many more there are; the rest are hidden
   * unless that title is in *open* (she asked to see them all). */
  function noticeFolds(titles, open) {
    const first = {}, count = {};
    (titles || []).forEach(function (t, i) {
      const k = String(t || "");
      count[k] = (count[k] || 0) + 1;
      if (!(k in first)) first[k] = i;
    });
    return (titles || []).map(function (t, i) {
      const k = String(t || "");
      const lead = first[k] === i;
      return { lead: lead, more: lead ? count[k] - 1 : 0, hidden: !lead && !(open && open.has && open.has(k)) };
    });
  }
  function foldWords(more, shown) {
    return { count: "+" + more + " MORE", button: shown ? "Hide the others" : "Show all " + (more + 1) };
  }

  /* Knowledge: what the builds read (GET /v1/knowledge/sources), grouped by source, with its credit. */
  const KNOWLEDGE_SOURCES = { wikipedia: "Wikipedia", gutenberg: "Project Gutenberg" };
  const KNOWLEDGE_EMPTY = "Nothing has been read yet. Find books above: Wikipedia and Gutenberg are read about what you ask, " +
                          "after your Yes on its plan.";
  function sizeWords(n) {
    n = Math.max(0, Number(n) || 0);
    if (n < 1000) return n + " bytes";
    if (n < 1e6) return Math.round(n / 1000) + " KB";
    return (n / 1e6).toFixed(1) + " MB";
  }
  function knowledgeCredit(source, license) {
    if (source === "wikipedia") return "Text from Wikipedia, by its authors, under " + (license || "CC BY-SA 4.0") + ".";
    if (source === "gutenberg") return "Project Gutenberg ebooks" + (license ? ", " + license.charAt(0).toLowerCase() + license.slice(1) : "") + ".";
    return license ? "License: " + license + "." : "";
  }
  function knowledgeGroups(data) {
    const items = data && Array.isArray(data.items) ? data.items : [];
    const by = {};
    let total = 0, count = 0;
    items.forEach(function (it) {
      if (!it || typeof it !== "object") return;
      const src = typeof it.source === "string" && it.source ? it.source : "other";
      const g = by[src] = by[src] || { source: src, name: KNOWLEDGE_SOURCES[src] || src, items: [], bytes: 0, licenses: [] };
      const lic = typeof it.license === "string" ? it.license : "";
      if (lic && g.licenses.indexOf(lic) < 0) g.licenses.push(lic);
      const size = Math.max(0, Number(it.size) || 0);
      g.bytes += size; total += size; count++;
      const url = typeof it.url === "string" && /^https:\/\/[^\s]+$/.test(it.url) ? it.url : "";
      g.items.push({ title: String(it.title || "Untitled"), file: typeof it.file === "string" ? it.file : "", url: url, host: url ? url.slice(8).split("/")[0] : "",
                     license: lic, size: sizeWords(size), date: String(it.fetched_at || "").slice(0, 10) });
    });
    const rank = function (s) { const i = Object.keys(KNOWLEDGE_SOURCES).indexOf(s); return i < 0 ? 99 : i; };
    const groups = Object.keys(by).sort(function (a, b) { return rank(a) - rank(b) || a.localeCompare(b); }).map(function (k) {
      const g = by[k];
      return Object.assign(g, { credit: knowledgeCredit(g.source, g.licenses[0] || ""), size: sizeWords(g.bytes), count: g.items.length });
    });
    return { groups: groups, count: count, size: sizeWords(total), empty: !groups.length };
  }

  /* Lane I19b: the library fetch, from GET /v1/toolsmith (each build's "library"), for Home, Building and
   * Knowledge. Said plainly: BuildBot downloads into her library on this computer; she is not on those sites. */
  const LIBRARY_HERE = "BuildBot is downloading these into your library on this computer. You are not on these websites.";
  const LIBRARY_DONE = "BuildBot downloaded these into your library on this computer. You are not on these websites.";
  const LIBRARY_LATER = "After your Yes, BuildBot downloads these into your library on this computer. You do not visit these websites.";
  const SOURCE_LINK_NOTE = "Opens the source website in a new tab of your browser.";
  function andList(xs) {
    const l = (Array.isArray(xs) ? xs : []).map(String).filter(Boolean);
    return l.length < 2 ? (l[0] || "") : l.slice(0, -1).join(", ") + " and " + l[l.length - 1];
  }
  function libraryLine(k, goal, id) {
    if (!k || typeof k !== "object") return null;
    const from = andList(k.sources) || "public sources";
    const n = Math.max(0, Number(k.done) || 0), of = Math.max(0, Number(k.of) || 0);
    const ofWords = (k.up_to ? "up to " : "") + of;
    const state = k.state === "fetching" || k.state === "done" ? k.state : "waiting";
    const line = state === "fetching" ? "Library: fetching " + n + " of " + ofWords + " from " + from
      : state === "done" ? "Library: downloaded " + n + " of " + of + " from " + from
        : "Library: " + ofWords + " from " + from + ", after your Yes";
    return { id: String(id || ""), goal: String(goal || ""), state: state, fetching: state === "fetching", line: line,
             now: state === "fetching" ? String(k.now || "") : "", end: state === "done" ? String(k.line || "") : "",
             note: state === "fetching" ? LIBRARY_HERE : state === "done" ? LIBRARY_DONE : LIBRARY_LATER, href: "#knowledge" };
  }
  /* The one library line for Home and Knowledge: a build fetching now (newest first), else the newest build that
   * fetched. Null when no build reads any knowledge, or none has started reading yet. */
  function libraryNow(status) {
    const builds = ((status && status.builds) || []).slice().sort(function (a, b) {
      return String(b.created_at || "").localeCompare(String(a.created_at || ""));
    });
    const pick = builds.find(function (b) { return b && b.library && b.library.state === "fetching"; })
      || builds.find(function (b) { return b && b.library && b.library.state === "done"; });
    return pick ? libraryLine(pick.library, pick.goal, pick.id) : null;
  }

  /* Lane I19b: the picture model a build uses (its plan's tick, GET /v1/toolsmith/builds/<id> use_fixed and
   * store_adds) and what it has done (GET /v1/models/used). The vision role READS pictures: it never makes one. */
  const PICTURE_WHAT = "It reads pictures and says what is in them. It does not make pictures.";
  const PICTURE_NONE = "Nothing has sent it a picture yet, so it has not read any. It reads a picture only when an app on your bot that reads pictures sends it one.";
  function describedBy(name) { return "Described by " + String(name || "your bot's model") + " on this computer."; }
  function pictureModel(b, used) {
    b = b || {};
    const fixed = b.use_fixed && b.use_fixed.models && typeof b.use_fixed.models === "object" ? String(b.use_fixed.models.vision || "") : "";
    const apps = b.store_adds && Array.isArray(b.store_adds.apps) ? b.store_adds.apps : [];
    const add = apps.find(function (a) { return a && a.kind === "model" && a.role === "vision"; }) || null;
    const needs = Array.isArray(b.model_needs) && b.model_needs.indexOf("vision") >= 0;
    const model = fixed || (add ? String(add.model || "") : "");
    if (!model && !needs) return null;
    const u = used && used.vision && typeof used.vision === "object" ? used.vision : null;
    if (!model) {
      return { model: "", name: "", head: "This goal may need a picture model. None was added for it, so a picture would go to "
                 + (u && u.model ? u.model + ", the chat model" : "the chat model") + ".", what: PICTURE_WHAT, use: "", now: "" };
    }
    const name = add && add.name ? String(add.name) : model;
    const added = add && add.state === "failed" ? " It was not added: " + String(add.line || "").replace(/\.$/, "") + "." : "";
    let use = "", now = "";
    if (u && u.model && u.model !== model) now = "It is not the picture model now: " + u.model + " is.";
    else if (u && Number(u.count) > 0) {
      const c = Number(u.count);
      use = "It has read " + c + (c === 1 ? " picture" : " pictures") + " so far" + (u.last_by ? ", last for " + u.last_by : "")
        + (u.last_at ? " on " + String(u.last_at).slice(0, 10) : "") + ". " + describedBy(name);
    } else if (u) use = PICTURE_NONE;
    return { model: model, name: name, head: "Picture model: " + name + (name !== model ? " (" + model + ")" : "") + ", on this computer." + added,
             what: PICTURE_WHAT, use: use, now: now };
  }

  /* Apps: an update's Yes card waiting for an installed app (lane I13: "<App> has an update"), by app id,
   * from GET /v1/consent?status=pending. An update card is a package card with what_changed. */
  function updatesWaiting(list) {
    const out = {};
    (Array.isArray(list) ? list : []).forEach(function (c) {
      if (!c || (c.status && c.status !== "pending")) return;
      const scope = c.scope || {}, card = c.card || {};
      if (scope.kind !== "package" || typeof scope.target !== "string" || !card.what_changed || typeof card.what_changed !== "object") return;
      out[scope.target] = { consentId: String(c.consent_id || c.id || ""), title: String(c.title || card.title || ""),
                            version: String(card.version || "") };
    });
    return out;
  }

  /* Lane I17: the Developer page's Models section, from GET /v1/models. Each installed model and what it is
   * used for; per role, the catalog rows that fit this computer (best first) with strength, RAM and size, and
   * whether each can be added (Add) or set (Use for). Plain data; the page sets it as text. */
  const MODEL_ROLES = ["chat", "code", "vision", "fast"];
  const ROLE_TITLES = { chat: "Chat", code: "Coding", vision: "Reading pictures", fast: "Small and fast" };
  function modelsView(d) {
    d = d || {};
    const roles = d.roles && typeof d.roles === "object" ? d.roles : {};
    const rows = Array.isArray(d.catalog) ? d.catalog : [];
    const byId = {};
    rows.forEach(function (r) { if (r && r.id) byId[r.id] = r; });
    const pulls = d.pulls && typeof d.pulls === "object" ? d.pulls : {};
    const installed = (Array.isArray(d.installed) ? d.installed : []).filter(function (m) { return m && m.name; }).map(function (m) {
      const used = (Array.isArray(m.used_for) ? m.used_for : []).map(function (r) { return ROLE_TITLES[r] || r; });
      return { name: String(m.name), size: m.size_gb ? m.size_gb + " GB" : "", usedFor: used.length ? used.join(", ") : "Not used yet" };
    });
    const sections = MODEL_ROLES.map(function (role) {
      const ids = d.recommended && Array.isArray(d.recommended[role]) ? d.recommended[role] : [];
      const current = String(roles[role] || "");
      return {
        role: role, title: ROLE_TITLES[role],
        now: current ? "Now: " + current : (role === "chat" ? "Now: none picked yet" : "Now: the chat model"),
        rows: ids.map(function (id) { return byId[id]; }).filter(Boolean).slice(0, 3).map(function (r) {
          const p = pulls[r.id] || {};
          const busy = p.state === "asking" || p.state === "downloading";
          const done = typeof p.done_gb === "number" && p.size_gb ? " (" + p.done_gb + " of " + p.size_gb + " GB)" : "";
          return { id: r.id, label: String(r.label || r.id), strength: String(r.strength || ""),
                   facts: "Needs " + r.min_ram_gb + " GB of RAM, best with " + r.rec_ram_gb + " GB. " + String(r.download || "")
                     + (r.fit === "tight" ? ". A tight fit here, so it may be slow." : "."),
                   canAdd: !r.installed && !busy, canUse: !!r.installed && current !== r.id,
                   using: current === r.id, said: p.line ? String(p.line) + done : "" };
        }),
        none: ids.length ? "" : "Nothing we list fits this computer for this yet.",
      };
    });
    return { installed: installed, sections: sections, problem: String(d.problem || ""), bridge: String(d.bridge || "") };
  }


  /* ---- The PuffPAI window (window.html, D81): rules with no DOM. ---- */
  /* The / command list. *words* are extra search words, so a command is found by what it does. The Desktop and the
   * terminal answer the same routes; nothing here names one app. Apps join through appCommands (the Registry). */
  const WINDOW_COMMANDS = [
    { name: "/show", args: "<app|build|turn|store>", what: "Show it in the side pane", words: "show me view preview open side pane" },
    { name: "/build", args: "<goal>", what: "Ask Toolsmith for something new", words: "make new app find github toolsmith" },
    { name: "/plan", args: "", what: "See the plan card again", words: "plan card yes" },
    { name: "/yes", args: "", what: "Say Yes to the card you can see", words: "consent approve accept card answer" },
    { name: "/no", args: "", what: "Say No to the card you can see", words: "consent refuse decline card answer" },
    { name: "/watch", args: "", what: "Follow a build live", words: "progress building status jobs" },
    { name: "/turn", args: "", what: "Your turn steps: copy, done, skip", words: "checklist todo step manual waiting" },
    { name: "/apps", args: "", what: "Installed apps and the store", words: "store list installed packages" },
    { name: "/store", args: "", what: "The PuffPAI store, in the side pane", words: "add install apps tools looks avatars skills packages" },
    { name: "/add", args: "<folder|url|id>", what: "Add an app, a folder of your code, or a GitHub repo", words: "install package repo github link folder" },
    { name: "/open", args: "<app>", what: "Open an app in your browser", words: "browser tab launch" },
    { name: "/dashboard", args: "", what: "Open the full dashboard in your browser", words: "desktop tiles web page full" },
    { name: "/know", args: "", what: "Search your knowledge and documents", words: "search documents files knowledge find" },
    { name: "/link", args: "", what: "Link a folder in place (never copied)", words: "folder data index knowledge" },
    { name: "/vault", args: "", what: "Vault status, open or locked", words: "password pin secret key recovery locked" },
    { name: "/unlock", args: "", what: "Open the vault", words: "password pin vault" },
    { name: "/lock", args: "", what: "Lock the vault", words: "password pin vault" },
    { name: "/bridge", args: "", what: "Open or closed", words: "internet network egress out ledger" },
    { name: "/brain", args: "", what: "Which local model answers here", words: "model ollama llm brain" },
    { name: "/people", args: "", what: "Who lives here", words: "family people users" },
    { name: "/look", args: "", what: "Change the look", words: "theme colors avatar style look skin dark light day night" },
    { name: "/keys", args: "", what: "API keys and Toolsmith's cloud model", words: "api key token openai anthropic google cloud secret company cost cap" },
    { name: "/settings", args: "", what: "Everything else", words: "settings options config" },
    { name: "/github", args: "", what: "Link GitHub, add code from GitHub", words: "github token repo code" },
    { name: "/dev", args: "", what: "Developer: check, new, logs, API, MCP", words: "developer check new logs api mcp package" },
    { name: "/clear", args: "", what: "Clear this window (the bot still remembers the chat)", words: "clean empty wipe screen" },
    { name: "/help", args: "<words>", what: "Search these commands", words: "help docs search how" },
  ];
  /* What the window's rail lists: every app and tool that is installed, a page or not. Where each one opens is the
   * frame route's answer (GET /v1/packages/<id>/frame), never decided here. */
  const NOT_APPS = ["ui", "avatar", "look", "wizard", "bundle", "provider"];
  function windowApps(rows) {
    return (rows || []).filter(function (r) { return r && r.status !== "removed" && NOT_APPS.indexOf(r.kind) < 0; }).map(function (r) {
      const ui = r.ui || {};
      const first = (ui.pages || [])[0];
      return { pkg: r.id, name: String(r.name || r.id), down: !(r.health && r.health.ok), firstPage: first && first.id ? String(first.id) : "",
               program: !!ui.open };
    });
  }
  /* One /show row per app, by its own name and description, so any word about an app finds it. */
  function appCommands(apps, rows) {
    return (apps || []).map(function (a) {
      const row = (rows || []).find(function (r) { return r.id === a.pkg; }) || {};
      return { name: "/show", args: a.name, what: "Show " + a.name, words: String(row.description || "") + " " + a.pkg, from: a.name };
    });
  }
  /* The rows that match *query* ("/pass", "/ api key"): every word must be somewhere in the row. */
  function commandHits(query, list) {
    const words = String(query || "").replace(/^\//, "").toLowerCase().split(/\s+/).filter(Boolean);
    return (list || []).filter(function (c) {
      const hay = [c.name, c.args, c.what, c.words, c.from || "Core"].join(" ").toLowerCase();
      return words.every(function (w) { return hay.indexOf(w) >= 0; });
    });
  }
  /* "/build a recipe box" -> {name: "/build", rest: "a recipe box"}; null for plain words. */
  function commandParse(text) {
    const m = /^(\/[A-Za-z]+)(?:\s+([\s\S]*))?$/.exec(String(text || "").trim());
    return m ? { name: m[1].toLowerCase(), rest: (m[2] || "").trim() } : null;
  }

  /* A brain's answer as plain blocks, drawn with text nodes only (never as HTML). Blocks: h, p, ul, ol, code.
   * Inline: text, b, i, code, link (http and https only; any other link stays plain words). */
  function mdInline(text) {
    const out = [];
    const re = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\s][^*\n]*\*)|(\[[^\]\n]+\]\([^)\s]+\))|(https?:\/\/[^\s<>()]+)/g;
    let at = 0, m;
    const s = String(text || "");
    while ((m = re.exec(s))) {
      if (m.index > at) out.push({ t: "text", text: s.slice(at, m.index) });
      if (m[1]) out.push({ t: "code", text: m[1].slice(1, -1) });
      else if (m[2]) out.push({ t: "b", text: m[2].slice(2, -2) });
      else if (m[3]) out.push({ t: "i", text: m[3].slice(1, -1) });
      else if (m[4]) {
        const l = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(m[4]);
        if (webUrl(l[2])) out.push({ t: "link", text: l[1], url: l[2] });
        else out.push({ t: "text", text: l[1] });
      } else {
        const bare = m[5].replace(/[.,;:!?]+$/, "");
        if (webUrl(bare)) out.push({ t: "link", text: bare, url: bare });
        else out.push({ t: "text", text: bare });
        if (bare.length < m[5].length) out.push({ t: "text", text: m[5].slice(bare.length) });
      }
      at = m.index + m[0].length;
    }
    if (at < s.length) out.push({ t: "text", text: s.slice(at) });
    return out;
  }
  function mdBlocks(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let para = [], list = null, code = null;
    const endPara = function () { if (para.length) out.push({ type: "p", inline: mdInline(para.join(" ")) }); para = []; };
    const endList = function () { if (list) out.push(list); list = null; };
    lines.forEach(function (line) {
      if (code) {
        if (/^\s*```/.test(line)) { out.push({ type: "code", text: code.join("\n") }); code = null; } else code.push(line);
        return;
      }
      if (/^\s*```/.test(line)) { endPara(); endList(); code = []; return; }
      const h = /^(#{1,4})\s+(.*)$/.exec(line);
      const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
      const ol = /^\s*\d+[.)]\s+(.*)$/.exec(line);
      if (h) { endPara(); endList(); out.push({ type: "h", level: h[1].length, inline: mdInline(h[2]) }); }
      else if (ul || ol) {
        endPara();
        const kind = ul ? "ul" : "ol";
        if (!list || list.type !== kind) { endList(); list = { type: kind, items: [] }; }
        list.items.push(mdInline((ul || ol)[1]));
      } else if (!line.trim()) { endPara(); endList(); }
      else { endList(); para.push(line.trim()); }
    });
    if (code) out.push({ type: "code", text: code.join("\n") });     // a code block still streaming
    endPara(); endList();
    return out;
  }

  /* Only a plain web address may open outside: http or https, with no sign-in in it. */
  function webUrl(url) {
    let u;
    try { u = new URL(String(url || "")); } catch (e) { return false; }
    return (u.protocol === "http:" || u.protocol === "https:") && !u.username && !u.password;
  }
  /* The Registry row an address from the chat's open event belongs to, or null: a package's own origin (D53), its
   * own port on this computer, or a page's own url. */
  function openMatch(url, rows, base) {
    if (!webUrl(url)) return null;
    const u = new URL(String(url));
    const live = (rows || []).filter(function (r) { return r && r.status !== "removed"; });
    const host = u.hostname.toLowerCase();
    if (isNetwork(base)) {
      const id = Object.keys(base.ports).find(function (k) { return pkgOrigin(k, base) === u.origin; });
      if (id) return live.find(function (r) { return r.id === id; }) || null;
    }
    const own = /^([a-z][a-z0-9-]{1,39})\.localhost$/.exec(host);
    if (own && u.origin === pkgOrigin(own[1], base)) return live.find(function (r) { return r.id === own[1]; }) || null;
    if (host === "127.0.0.1" || host === "localhost" || host === "[::1]") {
      const hit = live.find(function (r) { return r.port && String(r.port) === u.port; });
      if (hit) return hit;
    }
    return live.find(function (r) {
      return ((r.ui || {}).pages || []).some(function (p) {
        if (!p || !p.url) return false;
        try { return new URL(String(p.url).replace("{port}", String(r.port || 0))).origin === u.origin; } catch (e) { return false; }
      });
    }) || null;
  }
  /* The apps a turn named: by name or id in its words, or by a tool it used ("<id>.<tool>"). In the order named. */
  function appsNamed(text, tools, apps) {
    const low = " " + String(text || "").toLowerCase() + " ";
    const at = function (word) {
      const m = new RegExp("[^a-z0-9]" + String(word).toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "[^a-z0-9]").exec(low);
      return m ? m.index : -1;
    };
    const used = (tools || []).map(String);
    return (apps || []).map(function (a) {
      const place = [at(a.name), at(a.pkg)].filter(function (i) { return i >= 0; });
      const byTool = used.some(function (t) { return t.indexOf(a.pkg + ".") === 0; });
      return { app: a, place: place.length ? Math.min.apply(null, place) : byTool ? low.length : -1 };
    }).filter(function (x) { return x.place >= 0; })
      .sort(function (x, y) { return x.place - y.place; }).map(function (x) { return x.app; });
  }
  /* What /show <words> means: an app, Building, Your turn or Settings. */
  function showTarget(words, apps) {
    const w = String(words || "").toLowerCase().replace(/^(me\s+)?((my|the)\s+)?/, "").trim();
    if (!w) return { kind: "none" };
    if (/^build(s|ing)?$/.test(w)) return { kind: "building" };
    if (/^(your\s+)?turn$/.test(w)) return { kind: "turn" };
    if (/^settings?$/.test(w)) return { kind: "settings" };
    if (/^(puffpai\s+)?store$/.test(w)) return { kind: "store" };
    const list = apps || [];
    const same = list.filter(function (a) { return a.pkg.toLowerCase() === w || String(a.name).toLowerCase() === w; });
    const hit = same.length ? same : list.filter(function (a) {
      return a.pkg.toLowerCase().indexOf(w) === 0 || String(a.name).toLowerCase().indexOf(w) === 0;
    });
    if (hit.length === 1) return { kind: "app", app: hit[0] };
    return hit.length ? { kind: "many", apps: hit } : { kind: "none" };
  }
  /* A build's chip in the rail (one row of GET /v1/toolsmith). tone: live, wait, ok, stop or dim. *waiting*: its plan
   * card waits for her Yes (the row itself does not say). */
  function buildChip(b, waiting) {
    const jobs = (b && b.jobs) || [];
    const has = function (s) { return jobs.some(function (j) { return String(j.status || "") === s; }); };
    if (waiting) return { words: "waiting on you", tone: "wait" };
    if (b && b.books_only) {                     // no jobs: what it read says how it went
      const k = b.library || {};
      if (b.planning) return { words: "looking", tone: "live" };
      if (k.state === "fetching") return { words: "reading", tone: "live" };
      if (k.state === "done") return { words: "done", tone: "ok" };
      if (b.yes) return { words: "starting", tone: "live" };
      return { words: k.state ? "no Yes" : "nothing found", tone: "dim" };
    }
    if (jobs.some(function (j) { return String(j.status || "").indexOf("building") === 0; })) return { words: "building", tone: "live" };
    if (has("waiting_yes") || has("waiting_you") || has("waiting_vault")) return { words: "waiting on you", tone: "wait" };
    if (has("planned") || !jobs.length && !(b && b.ended)) return { words: "starting", tone: "live" };
    if (jobs.some(function (j) { return AGAIN_STATES.indexOf(j.status) >= 0; })) return { words: "stopped", tone: "stop" };
    if (jobs.length && jobs.every(function (j) { return j.status === "working"; })) {
      const reading = jobs.filter(function (j) { return j.library === true; }).length;   // D123
      if (reading) return { words: reading === jobs.length ? "no apps yet" : "some have no app", tone: "dim" };
      return { words: "done", tone: "ok" };
    }
    if (has("said_no") || has("no_answer")) return { words: "no Yes", tone: "dim" };
    return { words: jobs.length ? "ended" : "no jobs", tone: "dim" };
  }
  /* Her list of chats, from this browser's saved list: "main" first (the Desktop's and the terminal's). */
  const MAIN_CHAT = "main";
  const CHAT_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;       // the Core's own rule (zone2/conversations.py)
  function chatList(saved) {
    const seen = {};
    const out = [{ id: MAIN_CHAT, title: "Main chat" }];
    seen[MAIN_CHAT] = true;
    (Array.isArray(saved) ? saved : []).forEach(function (c) {
      if (!c || typeof c.id !== "string" || !CHAT_ID.test(c.id)) return;
      if (c.id === MAIN_CHAT) { if (typeof c.title === "string" && c.title) out[0].title = c.title; return; }
      if (seen[c.id]) return;
      seen[c.id] = true;
      out.push({ id: c.id, title: typeof c.title === "string" && c.title ? c.title : "New chat" });
    });
    return out;
  }
  function chatTitle(text) {
    const s = String(text || "").split("\n")[0].replace(/\s+/g, " ").trim();
    return s.length > 40 ? s.slice(0, 39).trimEnd() + "..." : s;
  }
  function newChatId(rand) {
    return "c-" + String(rand || Math.random()).replace(/[^A-Za-z0-9]/g, "").slice(0, 16);
  }
  /* A sign-in code in the address (#pair=<code>), or "". */
  function pairCode(hash) {
    const m = /pair=([A-Za-z0-9-]+)/.exec(String(hash || ""));
    return m ? m[1] : "";
  }
  /* A card's short name, as the terminal says it (termchat.short). */
  function cardShort(id) {
    const s = String(id || "");
    return s.indexOf("c_") === 0 ? s.slice(2, 8) : s.slice(0, 6);
  }
  const YES_EXPLAIN = "A Yes card asks your Yes for exactly what it lists, nothing more. No means nothing is done. If the card " +
    "changes before you answer, your answer is refused and you see it again. You can answer here, in the dashboard or in the " +
    "terminal: the first answer counts.";
  const YES_EXPLAIN_FLOOR = "This card is floor-level, so it needs your PIN. No program can say Yes for you.";
  const YES_EXPLAIN_TICKS = "Tick what the card offers before you say Yes. Nothing ticked: it goes as planned.";

  const api = {
    // lane I19b
    libraryLine: libraryLine, libraryNow: libraryNow, andList: andList, LIBRARY_HERE: LIBRARY_HERE, LIBRARY_DONE: LIBRARY_DONE,
    LIBRARY_LATER: LIBRARY_LATER, SOURCE_LINK_NOTE: SOURCE_LINK_NOTE, pictureModel: pictureModel, describedBy: describedBy,
    PICTURE_WHAT: PICTURE_WHAT, PICTURE_NONE: PICTURE_NONE,
    // lane I17
    modelsView: modelsView, MODEL_ROLES: MODEL_ROLES, ROLE_TITLES: ROLE_TITLES, MODEL_TICK_NOTE: MODEL_TICK_NOTE,
    // lane I16
    storeProgress: storeProgress, STORE_STARTED: STORE_STARTED, installState: installState,
    personaSections: personaSections, personaView: personaView, PERSONA_NONE: PERSONA_NONE, PERSONA_REDO: PERSONA_REDO,
    PERSONA_DEFAULT: PERSONA_DEFAULT, YOU_LABELS: YOU_LABELS, YOU_LOCKED: YOU_LOCKED, YOU_INBOX: YOU_INBOX, youError: youError,
    youFlags: youFlags, youSaved: youSaved, timeZoneChoices: timeZoneChoices, swatchParts: swatchParts, storeSections: storeSections, noticeFolds: noticeFolds, foldWords: foldWords, LOOK_FUNCTIONS: LOOK_FUNCTIONS, sizeWords: sizeWords,
    passageView: passageView, knowledgeCredit: knowledgeCredit, knowledgeGroups: knowledgeGroups, KNOWLEDGE_EMPTY: KNOWLEDGE_EMPTY,
    updatesWaiting: updatesWaiting, planOf: planOf,
    STORE_TICK_NOTE: STORE_TICK_NOTE, STORE_LATER: STORE_LATER,
    pageTarget: pageTarget, trayItems: trayItems, widgetsOf: widgetsOf, widgetModel: widgetModel,
    stepCard: stepCard, consentCard: consentCard, consentAnswer: consentAnswer, installedRows: installedRows,
    healthText: healthText, openLabel: openLabel, bridgeOpen: bridgeOpen, bridgeView: bridgeView, chatWords: chatWords, cardTicks: cardTicks, TickMemory: TickMemory, OFFER_NOTE: OFFER_NOTE, HOLD_NOTE: HOLD_NOTE, BRIDGE_PIN_LINE: BRIDGE_PIN_LINE, WizardTranscript: WizardTranscript, wizardInput: wizardInput, isAdopted: isAdopted,
    isNetwork: isNetwork, networkBase: networkBase, networkOpen: networkOpen, networkView: networkView, ON_COMPUTER: ON_COMPUTER, onNetworkHost: onNetworkHost,
    UPLOAD_MAX: UPLOAD_MAX, UPLOAD_TYPES: UPLOAD_TYPES, UPLOAD_TOO_BIG: UPLOAD_TOO_BIG, uploadProblem: uploadProblem,
    BAD_WIDGET: BAD_WIDGET, OUTSIDE_PICTURE: OUTSIDE_PICTURE, ownPicture: ownPicture, raisedBy: raisedBy, adminAnswer: adminAnswer, onPages: onPages, pkgOrigin: pkgOrigin,
    PAGE_SANDBOX: PAGE_SANDBOX, WIDGET_SANDBOX: WIDGET_SANDBOX,
    APP_TILE: APP_TILE, homeApps: homeApps, stayers: stayers, appPageIds: appPageIds, frameSrcOk: frameSrcOk, frameSandbox: frameSandbox,
    // lane I8b
    themeFor: themeFor, RAIL: RAIL, railGroups: railGroups, comingLater: comingLater, LATER_TITLE: LATER_TITLE,
    HOME_HIDDEN: HOME_HIDDEN, storeApps: storeApps, storePrompt: storePrompt, storeNotNow: storeNotNow, storeShelf: storeShelf, STORE_TIER: STORE_TIER,
    STORE_TILE: STORE_TILE, STORE_LINE: STORE_LINE, STORE_MAX_HOME: STORE_MAX_HOME, firstLine: firstLine,
    BOT_NAME: BOT_NAME, botName: botName, greetingFor: greetingFor, helloLine: helloLine, OWN_THEMES: OWN_THEMES,
    themeTokens: themeTokens, themeTokensFor: themeTokensFor, themeList: themeList, themeById: themeById, jobWords: jobWords, jobGood: jobGood, jobActive: jobActive, buildView: buildView, buildingNow: buildingNow,
    buildStopped: buildStopped, buildReading: buildReading, againMore: againMore, DEV_EXAMPLES: DEV_EXAMPLES,
    runningSummary: runningSummary, brainView: brainView, whoOf: whoOf, ledgerLine: ledgerLine, auditLine: auditLine,
    drawbridgeChecks: drawbridgeChecks, arrangeTiles: arrangeTiles, movePosition: movePosition, seenTiles: seenTiles,
    addSource: addSource, booksWords: booksWords, planningWords: planningWords, BOOKS_CARD: BOOKS_CARD, buildTitle: buildTitle, findLive: findLive, availableApps: availableApps, appActions: appActions, ndjson: ndjson, ChatTurn: ChatTurn,
    stepWords: stepWords, ShellLog: ShellLog, SHELL_NOTE: SHELL_NOTE, AGENTS: AGENTS, agentRows: agentRows,
    vaultView: vaultView, personId: personId, slotRows: slotRows,
    faceFor: faceFor, FACE_SANDBOX: FACE_SANDBOX, OWN_FACE: OWN_FACE,
    shellSaid: shellSaid, ctrlcSaid: ctrlcSaid, shellLine: shellLine, SHELL_MAX_LINE: SHELL_MAX_LINE,
    shellState: shellState, SHELL_CLOSED: SHELL_CLOSED,
    // lane I19a
    SIGN_IN_TITLE: SIGN_IN_TITLE, signInWords: signInWords, signedOut: signedOut, NOT_BUILT: NOT_BUILT,
    VAULT_WAIT: VAULT_WAIT, VAULT_WAIT_HOME: VAULT_WAIT_HOME, VAULT_WAIT_WINDOW: VAULT_WAIT_WINDOW, vaultWaiting: vaultWaiting,
    stillWords: stillWords, stillWorking: stillWorking, setupAlive: setupAlive, POLL_RETRY: POLL_RETRY, POLL_WAIT_MS: POLL_WAIT_MS, pollCanRetry: pollCanRetry,
    buildKeys: buildKeys, UNLOCK_FOR_YES: UNLOCK_FOR_YES, UNLOCK_WAITING: UNLOCK_WAITING, UNLOCK_TYPE_PIN: UNLOCK_TYPE_PIN,
    // the PuffPAI window (D81)
    WINDOW_COMMANDS: WINDOW_COMMANDS, windowApps: windowApps, appCommands: appCommands, commandHits: commandHits, commandParse: commandParse,
    mdBlocks: mdBlocks, mdInline: mdInline, webUrl: webUrl, openMatch: openMatch, appsNamed: appsNamed, showTarget: showTarget,
    buildChip: buildChip, MAIN_CHAT: MAIN_CHAT, chatList: chatList, chatTitle: chatTitle, newChatId: newChatId,
    pairCode: pairCode, cardShort: cardShort, YES_EXPLAIN: YES_EXPLAIN, YES_EXPLAIN_FLOOR: YES_EXPLAIN_FLOOR, YES_EXPLAIN_TICKS: YES_EXPLAIN_TICKS,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffLogic = api;
})(typeof window !== "undefined" ? window : this);
