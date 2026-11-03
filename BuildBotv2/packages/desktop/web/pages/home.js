/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/page.tsx (adapted: the greeting, the tile
 * board and its arrange tray, on v2's routes). Home: your apps and whether they run, what Toolsmith builds now,
 * Your turn, each app's widgets, and the Bridge and the brain when she shows them (the top bar says both). Pin, hide, move and rename are kept in
 * data\desktop\layout.json through /v1/settings/layout. D82 (PROPOSED): Your apps, one launcher per
 * installed app, and app/<id> tiles that show an app's own page, when she puts it there (here, on Apps
 * or in chat) or picks "Apps on Home: their pages". */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el;
  let arranging = false, lastSeen = "";
  // App page tiles drawn last time, by tile id: {key: its frame's address, node}. Home redraws every 30 s and on
  // events; a frame whose address did not change keeps its node, so the app inside is never reloaded.
  const shown = new Map();

  async function coreTiles(apps, layout) {
    const [tool, check, cons, bridge, model] = await Promise.all([
      P.api("GET", "/v1/toolsmith"), P.api("GET", "/v1/checklist"), P.pendingConsents(),
      P.api("GET", "/v1/bridge"), P.api("GET", "/v1/model")]);
    const out = [];

    // Your apps (D82): every installed app, one click to open it, with Caretaker's health on its dot
    const sum = L.runningSummary(P.S.rows);
    const t0 = P.tile({ id: "apps", icon: "apps", title: "Your apps", wide: true,
                        stat: apps.length + (apps.length === 1 ? " APP" : " APPS") + (apps.length ? ", " + sum.running + " RUNNING" : "") });
    const onHome = L.appPageIds(apps, layout);
    if (!apps.length) t0.body.appendChild(el("p", { class: "empty", text: "No apps yet. Add one from Apps, or ask for one in chat." }));
    else t0.body.appendChild(el("div", { class: "launchers" }, apps.map((a) => el("div", { class: "launch" }, [
      el("button", { class: "launcher", title: a.down ? a.name + ": not working right now" : a.name, "data-app": a.pkg,
                     on: { click: () => P.openTarget(a) } },
        [P.pic(a.icon, "widgets"), el("span", { class: "name", text: a.name }),
         el("span", { class: "dot " + (a.running && !a.down ? "ok" : a.running ? "bad" : "") })]),
      arranging && a.page ? el("button", { class: "link small", "data-home": a.pkg,
        on: { click: () => arrange(onHome.indexOf(a.pkg) >= 0 ? "remove_app" : "add_app", null, { app: a.pkg }) } },
        [onHome.indexOf(a.pkg) >= 0 ? "Take its tile off Home" : a.target.kind === "desktop" ? "Show its page on Home" : "Give it a tile on Home"]) : null]))));
    out.push({ id: "apps", name: "Your apps", node: t0.node });

    // Building now: Toolsmith's builds, with each job's own "now" line
    const t2 = P.tile({ id: "building", icon: "construction", title: "Building now" });
    if (tool.status !== 200) t2.body.appendChild(el("p", { class: "empty", text: P.said(tool, "Toolsmith did not answer.") }));
    else {
      const now = L.buildingNow(tool.data);
      t2.head.appendChild(el("span", { class: "stat", text: now.live.length ? now.live.length + " LIVE" : "IDLE" }));
      const vaultWait = L.vaultWaiting(tool.data);            // D113: said first, with where to go
      if (vaultWait) t2.body.appendChild(el("p", { class: "warn-t", "data-vault-wait": vaultWait }, [L.VAULT_WAIT + " ",
        el("a", { href: "#building", text: L.VAULT_WAIT_HOME })]));
      const stopped = now.live.length ? null : L.buildStopped(tool.data);
      if (stopped) {                          // lane I18c: a build that stopped never looks finished
        t2.head.lastChild.textContent = "STOPPED";
        t2.body.appendChild(el("p", { class: "warn-t", "data-build-stopped": stopped.id }, [stopped.head + " ",
          el("a", { href: stopped.link, text: stopped.linkText })]));     // lane I18e: to its "Check again"
      } else if (!now.live.length && L.buildReading(tool.data)) {     // D123: no apps yet, never "all done"
        const reading = L.buildReading(tool.data);
        t2.body.appendChild(el("p", { "data-build-reading": reading.id }, [reading.head + " " + reading.next + " ",
          el("a", { href: reading.link, text: reading.linkText })]));
      } else if (!now.live.length) t2.body.appendChild(el("p", { class: "empty", text: "Toolsmith is not building anything right now." }));
      for (const b of now.live.slice(0, 2)) {
        const full = await P.api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(b.id));
        const v = full.status === 200 ? L.buildView(full.data) : null;
        t2.body.appendChild(el("p", {}, [el("b", { text: b.goal || "A build" })]));
        if (v && v.now) t2.body.appendChild(el("p", { class: "now", text: v.now }));
        t2.body.appendChild(el("ul", { class: "lines" }, (v ? v.jobs : b.jobs).flatMap((j) => [el("li", {}, [
          el("span", { class: "dot " + (j.active ? "live" : "") }), el("span", { class: "grow", text: j.name }),
          el("span", { class: "faint small", text: j.words })])].concat(j.now ? [el("li", { class: "now", text: j.now })] : []))));
      }
      const lib = L.libraryNow(tool.data);        // lane I19b: the library fetch, easy to find from Home
      if (lib) [el("p", { "data-library": lib.id }, [el("b", { text: lib.line + ". " }), el("a", { href: lib.href, text: "Open your library" })]),
        lib.now ? el("p", { class: "now", text: lib.now }) : null, el("p", { class: "small muted", text: lib.note })]
        .forEach((n) => n && t2.body.appendChild(n));
      t2.body.appendChild(el("div", { class: "row end" }, [el("a", { href: "#building", text: "Every build" })]));
    }
    out.push({ id: "building", name: "Building now", node: t2.node });

    // Your turn
    const t3 = P.tile({ id: "your-turn", icon: "task_alt", title: "Your turn" });
    const steps = check.status === 200 && Array.isArray(check.data) ? check.data : [];
    const n = steps.length + cons.cards.length;
    const quiet = tool.status === 200 && !L.buildingNow(tool.data).live.length;
    const halted = quiet ? L.buildStopped(tool.data) : null;
    t3.head.appendChild(el("span", { class: "stat", text: n ? n + " WAITING" : halted ? "BUILD STOPPED" : quiet && L.buildReading(tool.data) ? "NOTHING WAITING" : "ALL DONE" }));
    if (!n) t3.body.appendChild(el("p", { class: "empty", text: halted ? "Nothing else for you to do right now." : "Nothing for you to do right now." }));
    t3.body.appendChild(el("ul", { class: "lines" }, cons.cards.slice(0, 3).map((c) => el("li", {}, [P.icon("how_to_reg"), el("span", { text: c.title })]))
      .concat(steps.slice(0, 3).map((s) => el("li", {}, [P.icon("checklist"), el("span", { text: s.title })])))));
    if (n) t3.body.appendChild(el("div", { class: "row end" }, [el("a", { href: "#yourturn", text: "Open Your turn" })]));
    out.push({ id: "your-turn", name: "Your turn", node: t3.node });

    // The Bridge: one line from GET /v1/bridge
    const t4 = P.tile({ id: "bridge", icon: "public", title: "The Bridge" });
    if (bridge.status !== 200) t4.body.appendChild(el("p", { class: "empty", text: P.said(bridge, "The Bridge did not answer.") }));
    else {
      const bv = L.bridgeView(bridge.data);
      t4.head.appendChild(el("span", { class: "stat", text: bridge.data.state === "open" ? "OPEN" : "CLOSED" }));
      t4.body.appendChild(el("p", { text: bv.text }));
      const btn = await P.bridgeButton(bridge.data, () => { draw(document.getElementById("view-home")); P.drawChips(); P.drawPrivate(); });
      t4.body.appendChild(el("div", { class: "row" }, [btn, el("a", { href: "#outside", text: "What left your computer" })]));
    }
    out.push({ id: "bridge", name: "The Bridge", node: t4.node });

    // The brain: GET /v1/model
    const t5 = P.tile({ id: "brain", icon: "neurology", title: "The brain" });
    if (model.status !== 200) t5.body.appendChild(el("p", { class: "empty", text: P.said(model, "The brain did not answer.") }));
    else {
      const bv = L.brainView(model.data);
      t5.head.appendChild(el("span", { class: "stat", text: bv.words.toUpperCase() }));
      t5.body.appendChild(el("div", { class: "big", text: bv.name }));
      if (bv.reason) t5.body.appendChild(el("p", { class: "muted", text: bv.reason }));
      t5.body.appendChild(el("div", { class: "row end" }, [el("a", { href: "#settings/brain", text: "Change it" })]));
    }
    out.push({ id: "brain", name: "The brain", node: t5.node });
    return out;
  }

  /* An app's own page in its Home tile (D82). The Core says first whether it may be shown here
   * (GET /v1/packages/<id>/frame); otherwise the tile says why and keeps Open. */
  async function appTiles(apps, layout) {
    const ids = L.appPageIds(apps, layout);
    const frames = await Promise.all(ids.map((id) => P.api("GET", "/v1/packages/" + encodeURIComponent(id) + "/frame")));
    Array.from(shown.keys()).forEach((k) => { if (ids.indexOf(k.slice(L.APP_TILE.length)) < 0) shown.delete(k); });
    return ids.map((id, i) => {
      const a = apps.find((x) => x.pkg === id);
      const r = frames[i];
      const f = r.status === 200 && r.data ? r.data : { ok: false, url: "", line: P.said(r, a.name + "'s page did not answer.") };
      const stat = a.running ? (a.down ? "NOT WORKING" : "RUNNING") : "STOPPED";
      const framed = f.ok && L.frameSrcOk(f.url, window.location.href, P.S.pages);
      const key = framed ? f.url + " " + L.frameSandbox(f.sandbox || L.PAGE_SANDBOX) : "";
      const old = shown.get(L.APP_TILE + id);
      if (key && old && old.key === key) {
        old.node.querySelector(".tile-head .stat").textContent = stat;
        return { id: L.APP_TILE + id, name: a.name, node: old.node };
      }
      const t = P.tile({ id: L.APP_TILE + id, title: a.name, h: "h3", cls: "apppage", stat });
      t.head.insertBefore(el("span", { class: "appicon" }, [P.pic(a.icon, "widgets")]), t.head.firstChild);
      t.head.appendChild(el("button", { class: "link small", title: a.openLabel || "Open", on: { click: () => P.openTarget(a) } },
        [P.icon(a.target.kind === "tab" ? "open_in_new" : "open_in_browser"), "Open"]));
      if (framed) {
        t.body.appendChild(el("iframe", { class: "apppage", src: f.url, sandbox: L.frameSandbox(f.sandbox || L.PAGE_SANDBOX),
          title: a.name, loading: "lazy", referrerpolicy: "no-referrer" }));
      } else {
        t.body.appendChild(el("p", { class: "empty", text: f.line || a.name + "'s page can't be shown here." }));
        const row = el("div", { class: "row" });
        if (f.running === false || !a.running) row.appendChild(el("button", { class: "primary", on: { click: async () => {
          const s = await P.api("POST", "/v1/packages/" + encodeURIComponent(id) + "/start", {});
          if (s.status >= 400) P.notice({ title: a.name + " did not start", text: P.said(s) });
          draw(document.getElementById("view-home"));
        } } }, [P.icon("play_arrow"), "Start it"]));
        row.appendChild(el("button", { on: { click: () => P.openTarget(a) } }, [P.icon("open_in_new"), a.openLabel || "Open"]));
        t.body.appendChild(row);
      }
      if (key) shown.set(L.APP_TILE + id, { key, node: t.node });
      else shown.delete(L.APP_TILE + id);
      return { id: L.APP_TILE + id, name: a.name, node: t.node };
    });
  }

  async function widgetTiles() {
    const out = [];
    for (const w of L.widgetsOf(P.S.rows, P.S.pages)) {
      const t = P.tile({ id: w.key, icon: "widgets", title: w.title, stat: w.pkgName.toUpperCase(), h: "h3" });
      out.push({ id: w.key, name: w.title, node: t.node });
      if (w.type === "html") {
        t.body.appendChild(el("iframe", { class: "widget", src: w.src, sandbox: L.WIDGET_SANDBOX, title: w.title, loading: "lazy",
          referrerpolicy: "no-referrer" }));
        continue;
      }
      const r = await P.pageJson(w.src);
      const m = r.status === 200 ? L.widgetModel(w.type, r.data, { pkg: w.pkg, base: P.S.pages }) : { error: "This widget did not load." };
      if (m.error) { t.body.appendChild(el("p", { class: "empty", text: m.error })); continue; }
      if (w.type === "list" || w.type === "feed") { if (!m.items.length) { t.body.appendChild(el("p", { class: "empty", text: "Nothing here yet." })); continue; } }
      if (w.type === "list") t.body.appendChild(el("ul", {}, m.items.map((x) => el("li", { text: x }))));
      if (w.type === "number") t.body.appendChild(el("div", {}, [el("span", { class: "big", text: m.value }), " " + m.unit,
        m.label ? el("div", { class: "muted small", text: m.label }) : null]));
      if (w.type === "image" && m.outside) t.body.appendChild(el("p", { class: "empty", text: m.note }));   // never loaded
      if (w.type === "image" && !m.outside) t.body.appendChild(el("figure", {}, [el("img", { class: "pic", src: m.src, alt: m.caption }),
        m.caption ? el("figcaption", { class: "small muted", text: m.caption }) : null]));
      if (w.type === "feed") t.body.appendChild(el("ul", {}, m.items.map((it) => el("li", {}, [
        it.url ? el("a", { href: it.url, target: "_blank", rel: "noopener", text: it.title }) : it.title,
        it.time ? el("span", { class: "muted small", text: " " + it.time }) : null]))));
    }
    return out;
  }

  async function arrange(action, tile, extra) {
    const r = await P.api("POST", "/v1/settings/layout/arrange", Object.assign({ action, tile }, extra || {}));
    if (r.status !== 200) P.notice({ title: "Home did not change", text: P.said(r) });
    draw(document.getElementById("view-home"));
  }

  function editBar(t, ids) {
    const up = L.movePosition(ids, t.id, -1), down = L.movePosition(ids, t.id, 1);
    const app = t.id.indexOf(L.APP_TILE) === 0 ? t.id.slice(L.APP_TILE.length) : "";
    return el("div", { class: "editbar" }, [
      app ? el("button", { text: "Take off Home", on: { click: () => arrange("remove_app", null, { app }) } }) : null,
      el("button", { text: t.pinned ? "Unpin" : "Pin", on: { click: () => arrange(t.pinned ? "unpin" : "pin", t.id) } }),
      up ? el("button", { text: "Up", on: { click: () => arrange("move", t.id, { position: up }) } }) : null,
      down ? el("button", { text: "Down", on: { click: () => arrange("move", t.id, { position: down }) } }) : null,
      el("button", { text: "Hide", on: { click: () => arrange("hide", t.id) } }),
      el("button", { text: "Rename", on: { click: () => {
        const name = window.prompt("A name for this tile (empty gives its own name back)", t.name);
        if (name !== null) arrange("rename", t.id, { name });
      } } })]);
  }

  async function draw(sec) {
    if (!sec) return;
    const [, layoutR] = await Promise.all([P.loadRegistry(), P.api("GET", "/v1/settings/layout")]);
    const s = P.S.settings || {};
    const layout = layoutR.status === 200 ? layoutR.data : {};
    const apps = L.homeApps(P.S.rows, P.S.pages);
    const tiles = (await coreTiles(apps, layout)).concat(await appTiles(apps, layout), await widgetTiles());
    const seen = L.seenTiles(tiles);
    if (layoutR.status === 200 && JSON.stringify(seen) !== lastSeen) {      // so a tile can be found by her name for it
      lastSeen = JSON.stringify(seen);
      P.api("PUT", "/v1/settings/layout", { seen: { tiles: seen } });
    }
    const placed = L.arrangeTiles(tiles.map((t) => ({ id: t.id, name: t.name })), layout);
    const byId = Object.fromEntries(tiles.map((t) => [t.id, t.node]));
    const ids = placed.visible.map((t) => t.id);

    const hello = el("div", { class: "hello" }, [P.face("face"), el("div", {}, [
      el("h1", { text: L.helloLine(new Date().getHours(), s) }),
      el("p", { text: s.wizard_complete ? "Everything you added is in the menu. Ask me about any of it in chat."
                                        : "Setup is not finished yet. Open Setup in the menu and I'll walk you through it." }),
      el("div", { class: "row" }, [
        el("button", { class: "primary", on: { click: () => P.go("chat") } }, [P.icon("chat_bubble"), "Chat"]),
        el("button", { on: { click: () => P.go("apps", "available") } }, [P.icon("add_circle"), "Add an app"]),
        el("button", { class: arranging ? "primary" : "", on: { click: () => { arranging = !arranging; draw(sec); } } },
          [P.icon("dashboard_customize"), arranging ? "Done arranging" : "Arrange"])])])]);
    const grid = sec.querySelector(":scope > .tiles") || el("div", { class: "tiles board" });
    place(grid, placed.visible.map((t) => {
      const node = byId[t.id];
      node.querySelectorAll(":scope > .editbar, .tile-head > .pinmark").forEach((n) => n.remove());   // a kept app tile
      const h = node.querySelector(".tile-head h2, .tile-head h3");
      if (h) h.textContent = t.name;
      if (t.pinned) { const k = P.icon("keep"); k.classList.add("pinmark"); node.querySelector(".tile-head").appendChild(k); }
      if (arranging) node.insertBefore(editBar(t, ids), node.firstChild);
      return node;
    }));
    const parts = [hello, await storeCard(s, layout, sec), grid];
    if (arranging) parts.push(el("div", { class: "arrange-tray" }, [
      el("span", { class: "muted", text: "Apps on Home:" }),
      el("button", { class: (layout.apps || {}).view === "pages" ? "" : "primary", "data-apps-as": "tiles",
                     on: { click: () => arrange("apps_as", null, { view: "tiles" }) } }, [P.icon("apps"), "Small tiles"]),
      el("button", { class: (layout.apps || {}).view === "pages" ? "primary" : "", "data-apps-as": "pages",
                     on: { click: () => arrange("apps_as", null, { view: "pages" }) } }, [P.icon("web"), "Their pages"]),
      el("span", { class: "muted", text: "Pin, move, hide or rename each tile. It is kept for every browser." + (placed.hidden.length ? " Hidden:" : "") })]
      .concat(placed.hidden.map((t) => el("button", { on: { click: () => arrange("show", t.id) } }, [P.icon("add"), t.name])))
      .concat([el("span", { class: "grow" }),
        el("button", { on: { click: async () => {
          await P.api("PUT", "/v1/settings/layout", { tiles: { order: [], hidden: L.HOME_HIDDEN, pinned: [], names: {} }, apps: { view: "tiles", pages: [] } });
          draw(sec);
        } } }, [P.icon("restart_alt"), "Reset layout"]),
        el("button", { class: "primary", on: { click: () => { arranging = false; draw(sec); } } }, [P.icon("check_circle"), "Done"])])));
    place(sec, parts.filter(Boolean));
  }

  /* Put *nodes* in *parent* in order, moving as little as can be and a framed tile last of all: moving a
   * frame reloads the app inside, so a frame moves only when its own tile moved. */
  function place(parent, nodes) {
    Array.from(parent.childNodes).forEach((c) => { if (nodes.indexOf(c) < 0) c.remove(); });
    const now = Array.from(parent.childNodes);
    const keep = new Set(L.stayers(now.map((c) => nodes.indexOf(c)),
      now.map((c) => (c.querySelector && c.querySelector("iframe") ? 1000 : 1))).map((i) => now[i]));
    for (let i = nodes.length - 1; i >= 0; i--) if (!keep.has(nodes[i])) parent.insertBefore(nodes[i], nodes[i + 1] || null);
  }

  /* After setup, one question (lane I10a): "Add apps from the PuffPAI store?" Not now hides it, kept in
   * the layout file (the "store" tile, hidden). No store app left: no card. Home draws by itself, so the store's
   * rows here are the saved copy only, and this never reads the network (D96). */
  async function storeCard(s, layout, sec) {
    if (!s.wizard_complete || (((layout || {}).tiles || {}).hidden || []).indexOf(L.STORE_TILE) >= 0) return null;
    const [r, saved] = await Promise.all([P.api("GET", "/v1/packages/available"), P.api("GET", "/v1/store?saved=1")]);
    const q = L.storePrompt(s, layout, r.status === 200 ? r.data : [], saved.status === 200 ? saved.data : null);
    if (!q.show) return null;
    const card = el("div", { class: "card store-card", "data-tile": L.STORE_TILE }, [el("h3", { text: "Add apps from the PuffPAI store?" }),
      el("p", { class: "small muted", text: L.STORE_LINE })]
      .concat(q.apps.map((p) => P.storeRow(p, () => draw(sec))))
      .concat([el("div", { class: "row" }, [
        el("button", { on: { click: () => P.go("apps", "available") } }, [P.icon("storefront"), "Open the PuffPAI store"]),
        el("button", { class: "link", on: { click: async () => {
          card.remove();
          const r2 = await P.api("PUT", "/v1/settings/layout", L.storeNotNow(layout));
          if (r2.status !== 200) P.notice({ title: "Home did not keep that", text: P.said(r2) });
        } } }, ["Not now"])])]));
    return card;
  }

  let timer = null;
  const soon = () => { clearTimeout(timer); timer = setTimeout(() => P.redraw("home"), 600); };
  ["checklist.changed", "consent.requested", "consent.granted", "bridge.posture", "notify.posted", "registry.poll", "vault.locked", "vault.unlocked"].forEach((t) => P.on(t, soon));
  P.page("home", { title: "Home", draw: (sec) => draw(sec) });
})();
