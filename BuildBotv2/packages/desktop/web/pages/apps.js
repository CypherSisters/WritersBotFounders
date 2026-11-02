/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/apps/_components/AppsIndex.tsx and
 * dashboard/add/page.tsx (adapted: the installed list, the built-ins not installed yet, and Add, on v2's
 * packages and Toolsmith routes). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, el = P.el, clear = P.clear;
  const TABS = [["installed", "Installed"], ["available", "PuffPAI store"], ["add", "Add"]];

  function tabs(sec, cur) {
    return el("div", { class: "tabs" }, TABS.map(([id, label]) =>
      el("button", { class: id === cur ? "on" : "", on: { click: () => P.go("apps", id) } }, [label])));
  }

  async function installed(box) {
    const [rows, pending, layoutR, takesFiles] = await Promise.all([P.loadRegistry(), P.api("GET", "/v1/consent?status=pending"),
      P.api("GET", "/v1/settings/layout"), P.sendTargets()]);
    const updates = L.updatesWaiting(pending.status === 200 ? pending.data : []);   // lane I13's "<App> has an update"
    const list = L.installedRows(rows, P.S.pages);
    const t = P.tile({ icon: "apps", title: "Your apps and parts", stat: list.length + " INSTALLED", wide: true });
    if (!list.length) t.body.appendChild(el("p", { class: "empty", text: "Nothing installed yet. Add one from the PuffPAI store or Add." }));
    const items = L.trayItems(rows, P.S.pages);
    const apps = L.homeApps(rows, P.S.pages);
    const onHome = layoutR.status === 200 ? L.appPageIds(apps, layoutR.data) : null;   // D82: its own page on Home
    list.forEach((r) => {
      const raw = rows.find((x) => x.id === r.id) || {};
      const item = items.find((i) => i.pkg === r.id);
      const said = el("div", { class: "small" });
      const acts = el("div", { class: "acts" });
      if (item) acts.appendChild(el("button", { class: "primary", on: { click: () => P.openTarget(item) } },
        [P.icon(item.target.kind === "tab" ? "open_in_new" : "open_in_browser"), item.openLabel || "Open"]));
      const home = onHome && apps.find((a) => a.pkg === r.id && a.page);
      if (home) {
        const there = onHome.indexOf(r.id) >= 0;
        acts.appendChild(el("button", { "data-act": "home", on: { click: async () => {
          const h = await P.api("POST", "/v1/settings/layout/arrange", { action: there ? "remove_app" : "add_app", app: r.id });
          if (h.status !== 200) { clear(said).appendChild(el("p", { class: "fail", text: P.said(h) })); return; }
          P.notice({ title: (h.data || {}).line || "Home changed." });
          P.redraw("apps");
        } } }, [P.icon(there ? "remove_from_queue" : "add_to_queue"), there ? "Take off Home" : "Show on Home"]));
      }
      const takes = takesFiles.find((x) => x.id === r.id);
      if (takes) acts.appendChild(P.sendFileButton(takes, said));   // D127: an app that takes files (02 `accepts`)
      const update = updates[r.id];
      L.appActions(raw, !!update).forEach((a) => acts.appendChild(el("button", { class: a.id === "remove" ? "danger" : a.waiting ? "primary" : "",
        "data-act": a.id, on: { click: () => act(a.id, raw, said) } }, [P.icon(a.icon), a.label])));
      if (update) said.appendChild(el("p", { class: "now", text: (update.title || r.name + " has an update") + (update.version ? ": version " + update.version + "." : ".") }));
      const ic = P.pic(item && item.icon, r.kind === "avatar" ? "face" : r.kind === "tool" ? "build" : "widgets");
      t.body.appendChild(el("div", { class: "app-row" }, [el("div", { class: "icon" }, [ic]),
        el("div", {}, [el("div", { class: "name", text: r.name }),
          el("div", { class: "small muted", text: r.kind + (r.adopted ? ", an outside app" : "") + ", " + r.status + ". " + r.health + "." }),
          r.description ? el("div", { class: "small faint", text: r.description }) : null, said]),
        acts]));
    });
    box.appendChild(t.node);
  }

  async function act(id, row, said) {
    clear(said);
    const path = "/v1/packages/" + encodeURIComponent(row.id);
    if (id === "logs") {
      const r = await P.api("GET", path + "/logs?lines=200");
      said.appendChild(el("pre", { class: "box", text: r.status === 200 ? (String(r.data || "").trim() || "The log is empty.") : P.said(r) }));
      return;
    }
    if (id === "remove") {
      said.appendChild(el("div", { class: "card note high" }, [el("h3", { text: "Remove " + row.name + "?" }),
        el("p", { text: "Keep its data, so adding it again picks up where it was, or delete its data too." }),
        el("div", { class: "row" }, [
          el("button", { on: { click: () => remove(row, "keep", said) } }, ["Remove, keep its data"]),
          el("button", { class: "danger", on: { click: () => remove(row, "delete", said) } }, ["Remove and delete its data"]),
          el("button", { class: "link", on: { click: () => clear(said) } }, ["Cancel"])])]));
      return;
    }
    said.appendChild(el("p", { class: "muted", text: id === "start" ? "Starting..." : id === "stop" ? "Stopping..." : "Checking for an update..." }));
    const r = await P.api("POST", path + "/" + id, {});
    clear(said);
    if (r.status >= 400) { said.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
    if (id === "update" && r.data && r.data.consent_id) {
      said.appendChild(el("p", { text: "Say Yes to the update:" }));
      const box = el("div"); said.appendChild(box);
      const want = String((r.data.card && r.data.card.version) || "");
      P.consentInline(box, r.data.consent_id, async (answer) => {
        if (answer !== "yes") { P.redraw("apps"); return; }
        // The swap runs after the Yes: wait for the row's new version (or 2 minutes), then redraw.
        clear(said).appendChild(el("p", { class: "now", text: "Updating " + row.name + "..." }));
        for (let i = 0; i < 120; i++) {
          const now = (await P.loadRegistry()).find((x) => x.id === row.id);
          if (L.installState(now).final && (!want || String(now.version || "") === want)) break;
          await P.sleep(1000);
        }
        P.redraw("apps");
      });
      return;
    }
    if (id === "update" && r.data && r.data.status === "updated") { P.notice({ title: r.data.line || row.name + " was updated." }); P.redraw("apps"); return; }
    if (id === "update") said.appendChild(el("p", { text: (r.data && (r.data.said || r.data.line)) || "Nothing new to update." }));
    else P.redraw("apps");
  }

  async function remove(row, keep, said) {
    const r = await P.api("DELETE", "/v1/packages/" + encodeURIComponent(row.id) + "?keep_data=" + keep);
    clear(said);
    if (r.status === 200) { P.notice({ title: row.name + " is removed", text: keep === "keep" ? "Its data is kept." : "Its data is deleted." }); P.redraw("apps"); return; }
    said.appendChild(el("p", { class: "fail", text: P.said(r) }));
  }

  /* The PuffPAI store (lanes I10a, looks, D96): one panel with a section per kind. A row that ships built in adds
   * by its id; a row of the store's index adds as store:<id>, with the same one Yes card, and never loads its picture.
   * A pulled row says why and is not offered. */
  function storeRow(p, after) {
    const said = el("div", { class: "small" });
    const add = el("button", { class: "primary", on: { click: () => addPackage(p.add || p.id, false, said, after) } }, [P.icon("add"), "Add"]);
    return el("div", { class: "app-row", "data-store-row": p.id }, [
      el("div", { class: "icon" }, [P.pic(p.icon, p.glyph || "deployed_code")]),
      el("div", {}, [el("div", { class: "name", text: p.name }), p.line ? el("div", { class: "small muted", text: p.line }) : null,
        p.store && p.by ? el("div", { class: "small faint", text: p.by + "." }) : null,
        p.tierLine ? el("div", { class: "small", text: p.tierLine }) : null,
        p.from ? el("div", { class: "small faint", text: p.from }) : null,
        p.pulled ? el("p", { class: "small warn-t", text: p.pulled }) : null,
        el("details", { class: "small faint" }, [el("summary", { text: "About" }), el("p", { text: p.about })]), said]),
      el("div", { class: "acts" }, p.pulled ? [] : [add])]);
  }

  /* The store's sources: the official one, always on, then hers, each with Remove, and adding one. Adding one is
   * for the owner at a paired device; the Core says so when it is not. */
  function storeSources(sources) {
    const said = el("div", { class: "small" });
    const out = [el("h2", { class: "micro", text: "Where the store reads from" })];
    sources.forEach((s) => {
      const gone = el("button", { class: "link", on: { click: async () => {
        const r = await P.api("DELETE", "/v1/store/sources?source=" + encodeURIComponent(s.source));
        if (r.status === 200) { P.redraw("apps"); return; }
        clear(said).appendChild(el("p", { class: "fail", text: P.said(r) }));
      } } }, ["Remove"]);
      out.push(el("div", { class: "row" }, [el("span", { class: "small", text: s.official ? "The official PuffPAI store. Always on." : s.source + ". What it lists installs as Community." }),
        s.removable ? gone : null]));
    });
    const text = el("input", { type: "text", placeholder: "https://example.com/index.json, or a folder on this computer", "aria-label": "A store source" });
    const form = el("form", {}, [text, el("div", { class: "row" }, [el("button", { type: "submit" }, [P.icon("add"), "Add a source"])])]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!text.value.trim()) return;
      const r = await P.api("POST", "/v1/store/sources", { source: text.value.trim() });
      if (r.status === 201) { P.redraw("apps"); return; }
      clear(said).appendChild(el("p", { class: "fail", text: P.said(r) }));
    });
    return out.concat([form, said]);
  }

  /* D96: nothing is fetched until she opens this panel (GET /v1/store), and the built-in rows never wait for the
   * network: they are drawn first, and the store's rows join them when it answers. */
  async function available(box, fresh) {
    const r = await P.api("GET", "/v1/packages/available");
    const t = P.tile({ icon: "storefront", title: "PuffPAI store", wide: true });
    box.appendChild(t.node);
    if (P.missing(r)) { t.body.appendChild(P.later("a Core route listing the built-in packages (GET /v1/packages/available)")); return; }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
    const stat = el("span", { class: "stat" });
    t.head.appendChild(stat);
    const body = el("div");
    t.body.appendChild(body);
    let pressed = false;                         // a press on a row: the rows stay as they are under her hand
    body.addEventListener("click", (e) => { if (e.target.closest && e.target.closest("button")) pressed = true; });
    const draw = (answer, note, read) => {
      const shelf = L.storeShelf(r.data, answer);
      clear(body);
      stat.textContent = shelf.count + " READY TO ADD";
      body.appendChild(el("p", { class: "muted small", text: "Adding one shows its Yes card first. " + L.STORE_LINE }));
      if (note) body.appendChild(el("p", { class: "small faint", text: note }));
      shelf.lines.forEach((line) => body.appendChild(el("p", { class: "small faint", "data-store-line": "1", text: line })));
      if (!shelf.sections.some((s) => s.title === "Apps")) {
        body.appendChild(el("p", { class: "empty", text: shelf.lines.length || note ? "No app to add right now." : "Every app in the PuffPAI store is installed." }));
      }
      shelf.sections.forEach((s) => {
        body.appendChild(el("h2", { class: "micro", text: s.title }));
        if (s.line) body.appendChild(el("p", { class: "small muted", text: s.line }));
        s.items.forEach((p) => body.appendChild(storeRow(Object.assign({}, p, s.kindWord ? {} : { line: p.kind + ". " + p.line }))));
      });
      if (read) body.appendChild(el("div", { class: "row" }, [el("button", { on: { click: () => available(clear(box), true) } }, [P.icon("refresh"), "Check the store again"])]));
      if (answer) storeSources(shelf.sources).forEach((n) => body.appendChild(n));
    };
    draw(null, "Reading the store...");
    const s = await P.api("GET", "/v1/store" + (fresh ? "?fresh=1" : ""));
    if (!body.isConnected) return;               // she went on, or a newer draw took its place
    if (pressed) { body.insertBefore(el("p", { class: "small faint", text: "The store's own rows show when you open this page again." }), body.firstChild); return; }
    if (s.status === 200) { draw(s.data, "", true); return; }
    // The listing failed: she can still remove the source that may be the cause.
    const src = await P.api("GET", "/v1/store/sources");
    if (!body.isConnected || pressed) return;
    draw(src.status === 200 && src.data ? { packages: [], sources: src.data.sources } : null, "The store could not be read: " + P.said(s), true);
  }

  async function addPackage(source, link, said, after) {
    clear(said).appendChild(el("p", { class: "muted", text: "Checking it first..." }));
    const r = await P.api("POST", "/v1/packages", { source, link: !!link });
    clear(said);
    if (r.status !== 202 || !r.data || !r.data.consent_id) { said.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
    const box = el("div"); said.appendChild(box);
    P.consentInline(box, r.data.consent_id, async (answer) => {
      if (answer !== "yes") return;
      clear(said).appendChild(el("p", { class: "muted", text: "Installing..." }));
      // The install runs after the Yes: wait until its Registry row (the staged id) has a final status (never
      // "installing"; "broken" is not working), the way puffpai add waits, then redraw the list.
      const id = r.data.id;
      let done = L.installState(null);
      for (let i = 0; id && i < 1800 && !done.final; i++) {
        done = L.installState((await P.loadRegistry()).find((x) => x.id === id));
        if (!done.final) await P.sleep(1000);
      }
      if (done.final && !done.ok) P.notice({ title: done.line, high: true });
      if (after) after(); else P.redraw("apps");
    });
  }

  function add(box) {
    const t = P.tile({ icon: "add_circle", title: "Add something new", wide: true });
    const text = el("textarea", { rows: "3", placeholder: "Paste a GitHub address, a folder on this computer, or say what you want, like \"a recipe box for my family\"",
                                  "aria-label": "What to add" });
    const linkBox = el("input", { type: "checkbox" });
    const said = el("div");
    const go = el("button", { class: "primary", type: "submit" }, [P.icon("auto_awesome"), "Go"]);
    const form = el("form", {}, [text, el("label", { class: "opt" }, [linkBox,
      el("span", { class: "small", text: "A folder: run it where it is, never copied or deleted (link)" })]), el("div", { class: "row" }, [go])]);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const s = L.addSource(text.value);
      clear(said);
      if (s.kind === "empty") return;
      if (s.kind === "books") { P.askBooks(s.words); return; }       // Knowledge's business: nothing is planned here
      if (s.kind === "folder" || s.kind === "builtin") { addPackage(s.source, s.kind === "folder" && linkBox.checked, said); return; }
      go.disabled = true;
      said.appendChild(el("p", { class: "now", text: s.kind === "github" ? "Toolsmith is reading " + s.url + " and planning. This can take a few minutes; Building shows it live."
        : "Toolsmith is finding apps for that and planning. This can take a few minutes; Building shows it live." }));
      const r = await P.api("POST", "/v1/toolsmith/plan", s.plan);
      go.disabled = false;
      clear(said);
      if (r.status !== 201) { said.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      planCard(said, L.buildView(r.data));
    });
    t.body.append(el("p", { class: "small muted", text: "Toolsmith finds a ready-made app, tries it in a folder of its own, and shows you one plan before anything is installed." }), form,
      el("p", { class: "small muted", "data-find-on-knowledge": "" }, ["Looking for books or reference? ", el("button", { class: "link", type: "button", text: "Find books on Knowledge", on: { click: () => P.go("knowledge") } })]),
      said);   // the plan's Yes card sits outside the form
    box.appendChild(t.node);
    if (window.PuffGitHub) box.appendChild(window.PuffGitHub.block({ where: "apps" }));   // lane I18g: link GitHub, add code from GitHub
  }

  /* Toolsmith's plan card: what it would try for each job, then the ONE Yes (with its ticks). */
  function planCard(box, v, after) {
    const yesCard = v.consentId && !v.yes;
    if (!(v.booksOnly && yesCard)) box.appendChild(el("div", { class: "card" }, [el("h3", { text: "The plan: " + v.goal })]
      .concat(v.planLines.length ? v.planLines.map((l) => el("p", { text: l })) : [el("p", { class: "muted", text: v.said || "Toolsmith has no plan for this yet." })])
      .concat(v.booksOnly ? [] : [el("div", { class: "row" }, [el("a", { href: "#building", text: "Follow it on Building" })])])));
    if (yesCard) { const c = el("div"); box.appendChild(c); P.consentInline(c, v.consentId, after || (() => P.go("building"))); }
  }

  P.storeRow = storeRow;
  P.planCard = planCard;                       // the GitHub block (github.js) shows its plan the same way
  P.page("apps", {
    title: "Apps",
    async draw(sec, arg) {
      const cur = TABS.some(([id]) => id === arg) ? arg : "installed";
      const box = el("div", { class: "tiles" });
      clear(sec).append(tabs(sec, cur), box);
      if (cur === "installed") await installed(box);
      if (cur === "available") await available(box);
      if (cur === "add") add(box);
    },
  });
})();
