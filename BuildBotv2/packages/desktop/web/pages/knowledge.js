/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/knowledge/page.tsx, knowledge/MyData.tsx and
 * knowledge/BuildSources.tsx (adapted). Knowledge: search what is indexed on this computer (the search_knowledge
 * tool, POST /v1/chat/tool), her documents (Add documents, a whole folder, V3-1), and what your builds read (lane
 * I11: Wikipedia and Gutenberg after the plan Yes, listed from GET /v1/knowledge/sources, lane I16). */
(function () {
  "use strict";
  const P = window.Puff, L = P.L, D = window.PuffDev, el = P.el, clear = P.clear;

  function hits(data) {
    const r = data && (data.result !== undefined ? data.result : data);
    const list = Array.isArray(r) ? r : (r && (r.results || r.hits || r.items)) || [];
    return list.map((h) => typeof h === "string" ? { title: h, text: "" }
      : { title: String(h.title || h.file || h.name || h.path || h.source || "A match"), text: String(h.text || h.snippet || h.excerpt || ""), where: String(h.path || h.source || "") });
  }

  function searchTile() {
    const t = P.tile({ icon: "search", title: "Search your knowledge", wide: true });
    const q = el("input", { placeholder: "What are you looking for?", "aria-label": "Search", autocomplete: "off" });
    const out = el("div");
    const f = el("form", { class: "row", autocomplete: "off" }, [q, el("button", { class: "primary", type: "submit" }, [P.icon("search"), "Search"])]);
    const books = el("div");
    const nowBox = el("div", { "data-library-box": "" });     // after her Yes: what is being read now, until it ends
    const find = findBooks(books, (id) => watchRun(nowBox, id, drawAgain));
    q.classList.add("grow");
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!q.value.trim()) return;
      clear(out).appendChild(el("p", { class: "muted", text: "Searching..." }));
      const r = await P.api("POST", "/v1/chat/tool", { tool: "search_knowledge", args: { query: q.value.trim(), limit: 10 } });
      clear(out);
      if (r.status !== 200) { out.appendChild(el("p", { class: "fail", text: P.said(r, "The search did not run.") })); return; }
      const list = hits(r.data);
      if (!list.length) {
        const words = q.value.trim();
        out.append(el("p", { class: "empty", text: "Nothing found. Only your documents and your bot's library are searched." }),
          el("button", { "data-find-these": "", text: "Find books about \u201c" + words + "\u201d", on: { click: () => find(words) } }));
        return;
      }
      out.appendChild(el("ul", { class: "lines" }, list.map((h) => el("li", {}, [el("div", {}, [el("b", { text: h.title }),
        h.where && h.where !== h.title ? el("div", { class: "faint small", text: h.where }) : null, h.text ? el("div", { class: "small", text: h.text }) : null])]))));
    });
    t.body.append(f, out, books, nowBox);
    return t.node;
  }

  const FOLDER_FROM_DEVICE = "A folder is read where it is, so it can't come from this device. Type the path of a folder on your bot, or add its files one at a time.";
  /* D130: a file she opens on another device comes to that device, not to a screen she can't see. */
  async function fileToDevice(f) {
    let r;
    try {
      r = await fetch("/v1/knowledge/files/open", { method: "POST", credentials: "same-origin", headers: P.authHeaders({ "Content-Type": "application/json" }), body: JSON.stringify({ path: f.path }) });
    } catch (e) { return "BuildBot did not answer."; }
    if (!r.ok) { try { return (await r.json()).error || "That file did not open."; } catch (e) { return "That file did not open."; } }
    const url = URL.createObjectURL(await r.blob());
    const a = el("a", { href: url, download: f.name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    return f.name + " is on its way to this device.";
  }

  /* What a batch is doing lives here, not in a tile: the page can be drawn again at any moment while one runs. */
  let carried = [];     // what the last batch or Remove did, { text, fail }: every new tile shows it, until the page is drawn for her
  let adding = false;   // one dialog or batch at a time
  let ended = 0;        // batches ended: a tile begun before one ended has read an old list
  let shown = null;     // the newest tile, which may not be the one a batch began on, or on the page yet
  let library = null;   // the library tile: { node, seen, fetching, reader }
  let watching = null;

  /* The lines of the page, in place, so what she is typing or reading stays. A document added or removed elsewhere draws the
   * tile again. True while something is being read, so it looks again soon; null when the page has gone, so it stops. */
  async function refreshed() {
    if (!shown || !shown.node.isConnected) return null;
    if (adding) return true;
    const [docs, folders] = await Promise.all([P.api("GET", "/v1/knowledge/documents"), P.api("GET", "/v1/folders")]);
    if (!shown || !shown.node.isConnected) return null;
    if (adding || docs.status !== 200 || folders.status !== 200) return false;
    const rows = L.documentRows(docs.data.documents), f = D.foldersView(folders.data);
    const all = rows.map((d) => d.path).concat(f.linked.map((x) => x.path));
    if (all.length !== shown.rows.size || all.some((p) => !shown.rows.has(p))) {
      if (!shown.typed()) { const old = shown.node; old.replaceWith(await documentsTile()); }
    } else {
      rows.forEach((d) => { const n = shown.rows.get(d.path); n.textContent = d.line; n.className = d.ok || d.waiting ? "small" : "small warn-t"; });
      f.linked.forEach((x) => { shown.rows.get(x.path).textContent = x.line; });
    }
    let more = rows.some((d) => d.waiting) || f.linked.some((x) => x.waiting);
    if (library && library.node.isConnected) {
      const [r, tool] = await Promise.all([P.api("GET", "/v1/knowledge/sources"), P.api("GET", "/v1/toolsmith")]);
      const now = tool.status === 200 ? L.libraryNow(tool.data) : null;
      const fetching = !!(now && now.fetching), seen = r.status === 200 ? r.data.count : library.seen;
      if ((seen !== library.seen || fetching !== library.fetching) && !library.reader.firstChild && !library.busy()) {
        const old = library.node;
        old.replaceWith(await sourcesTile());
      }
      more = more || fetching;
    }
    return more;
  }
  /* While she can see the page: soon while something is being read, else every 15 seconds. Coming back to it starts it again. */
  function watch(ms) {
    clearTimeout(watching);
    watching = setTimeout(async () => {
      const more = await refreshed().catch(() => false);
      if (more !== null && !document.hidden && shown.node.offsetParent !== null) watch(more ? 3000 : 15000);
    }, ms);
  }

  /* While a dialog or batch runs, nothing in the tile can draw it again and lose the lines. */
  const lock = (on) => shown.node.querySelectorAll("button, input").forEach((n) => { n.disabled = on; });

  /* D146: no vault for one pinned document. Off until she turns it on, for this document only. */
  function fictionSwitch(d, act) {
    const box = el("input", { type: "checkbox", "data-fiction": d.path, "aria-label": "No vault for this document: " + d.name });
    box.checked = d.fiction === true;
    box.addEventListener("change", async () => {
      if (await act("/v1/knowledge/pinned/fiction", { path: d.path, on: box.checked }, "") === false) box.checked = !box.checked;
    });
    return el("div", {}, [el("label", { class: "row small" }, [box, el("b", { text: "No vault for this document" })]),
      d.fiction === true ? null : el("div", { class: "faint small", text: "When this is on, it is an ordinary file: nothing in it is checked or kept in your vault, and it reads the same with your vault open or locked." })]);
  }

  /* V3-1: named in every chat, or only found when she asks. */
  function namedSwitch(d, act) {
    const box = el("input", { type: "checkbox", "data-named": d.path, "aria-label": "Name it in every chat: " + d.name });
    box.checked = d.named;
    box.addEventListener("change", async () => {
      if (await act("/v1/knowledge/documents/named", { path: d.path, on: box.checked }, "Reading it through your privacy check...") === false) box.checked = !box.checked;
    });
    return el("label", { class: "row small" }, [box, el("b", { text: "Name it in every chat" })]);
  }

  /* An original goes from the list at once. A copy's file is deleted, so that asks once more on the same button. */
  function removeDocument(d, act) {
    let sure = !d.copy;
    const b = el("button", { class: "link", "data-remove-document": d.path, text: "Remove", on: { click: () => {
      if (!sure) { sure = true; b.textContent = "Delete this copy from your bot?"; return; }
      act("/v1/knowledge/documents/remove", { path: d.path }, "");
    } } });
    return b;
  }

  /* V3-1: her documents, in one obvious place. Add documents takes many files, from the bot's computer or the device
   * she is at, and says what happened to each. Whole folders are read where they are (D126). */
  async function documentsTile() {
    const t = P.tile({ icon: "folder_open", title: "Your documents", id: "folders", wide: true });
    const began = ended;
    const [docs, folders, targets] = await Promise.all([P.api("GET", "/v1/knowledge/documents"), P.api("GET", "/v1/folders"), P.sendTargets()]);
    if (began !== ended) return documentsTile();
    const rows = L.documentRows(docs.status === 200 && docs.data ? docs.data.documents : []);
    const f = D.foldersView(folders.status === 200 ? folders.data : {});
    const here = !L.onNetworkHost(location.hostname);
    const lineEls = new Map();
    const rowLine = (p, cls, text) => { const n = el("div", { class: cls, text: text }); lineEls.set(p, n); return n; };
    const said = el("div", { class: "small", "data-said": "documents" }, carried.map((l) => el("p", { class: l.fail ? "fail warn-t" : null, text: l.text })));
    t.head.appendChild(el("span", { class: "stat", text: L.documentsHead(rows.length, f.linked.length) }));

    const redraw = async () => { const old = shown.node; old.replaceWith(await documentsTile()); };
    async function act(path, body, busy) {
      said.textContent = busy;
      const x = await P.api("POST", path, body);
      if (x.status !== 200) { said.textContent = P.said(x); return false; }
      carried = x.data && x.data.said ? [{ text: x.data.said, fail: false }] : [];
      await redraw();
    }
    /* Each in turn, then one line each. Drawn again only when all are done, so no line is lost. */
    async function addAll(items, send) {
      adding = true;
      carried = [];
      lock(true);
      carried = await L.addInTurn(items, send, (line) => { shown.said.textContent = line; });
      adding = false;
      ended++;
      await redraw();
    }
    async function addPath(d) {
      const x = await P.api("POST", "/v1/knowledge/documents", { path: d.path });
      return x.status === 200 ? { said: (x.data || {}).said, ok: (x.data || {}).ok } : { error: P.said(x) };
    }
    async function dialog(kind) {
      adding = true;
      carried = [];
      lock(true);
      const got = await P.pickPath(kind, said);
      adding = false;
      lock(false);
      return got;
    }

    const fromDevice = P.uploadInput(addAll);
    const add = el("button", { class: "primary", "data-add": "documents", on: { click: async () => {
      if (!here) { fromDevice.click(); return; }    // at once: a browser opens its picker only on a click
      const got = await dialog("files");
      if (got && got.upload) { fromDevice.hidden = false; fromDevice.click(); return; }   // shown too, if it won't open by itself
      if (got) addAll(got.map((p) => ({ name: L.fileNameOf(p), path: p })), addPath);
    } } }, [P.icon("add"), "Add documents"]);

    const path = el("input", { class: "grow", type: "text", placeholder: "C:\\Users\\you\\Documents\\Recipes", autocomplete: "off", spellcheck: "false", "aria-label": "A folder to add" });
    const link = el("button", { text: "Add this folder", on: { click: () => {
      const p = D.folderPath(path.value);
      if (p.ok) act("/v1/folders/link", { path: p.path }, "Adding it..."); else said.textContent = p.line;
    } } });
    const typed = el("details", { hidden: "hidden", "data-folder-typed": "" }, [el("summary", { class: "small faint", text: "Or type its path" }), el("div", { class: "row" }, [path, link])]);
    const folder = el("button", { class: "link", "data-add": "folder", text: "Add a whole folder", on: { click: async () => {
      typed.hidden = false;
      if (!here) { said.textContent = FOLDER_FROM_DEVICE; typed.open = true; return; }
      const got = await dialog("folder");
      if (got) act("/v1/folders/link", { path: got }, "Adding it..."); else if (said.textContent) typed.open = true;
    } } });

    t.body.append(el("div", { class: "row" }, [add, fromDevice, folder]), said, typed,
      el("p", { class: "faint small", "data-add-note": "", text: "Add documents: the files you pick. Add a whole folder: every document in it, read where it is, never copied, and new ones as they come." }),
      el("p", { class: "faint small", text: "It can be a .txt, .md, .docx or .pdf file. A file sent from a device can be up to 50 MB." }));
    if (docs.status !== 200) t.body.appendChild(el("p", { class: "fail", text: P.said(docs) }));
    else if (!rows.length) t.body.appendChild(el("p", { class: "empty", text: "Nothing here yet. Add a document and your bot can find it." }));
    else t.body.append(el("p", { class: "faint small", "data-named-note": "", text: "Name it in every chat: your bot keeps its name in mind and can read it start to finish." }),
      el("ul", { class: "lines docs", "data-documents": "list" }, rows.map((d) => el("li", { "data-document": d.path }, [
      el("div", { class: "grow" }, [el("b", { text: d.name }), el("div", { class: "faint small", text: d.path }), rowLine(d.path, d.ok || d.waiting ? "small" : "small warn-t", d.line),
        d.copyNote ? el("div", { class: "faint small", text: d.copyNote }) : null, namedSwitch(d, act), d.named ? fictionSwitch(d, act) : null]),
      el("div", { class: "acts" }, [P.sendPathButton(d.path, d.name, targets, said), removeDocument(d, act)])]))));
    if (f.linked.length) t.body.append(el("h3", { class: "micro", text: "Folders" }), el("ul", { class: "lines docs", "data-documents": "linked" }, f.linked.map((x) => el("li", { "data-linked": x.path }, [
      el("div", { class: "grow" }, [el("code", { text: x.path }), rowLine(x.path, "small", x.line),
        x.files.length ? el("details", { "data-folder-files": x.path }, [el("summary", { class: "small", text: targets.length ? "See its files, and send one to an app" : "See its files" }),
          el("ul", { class: "lines" }, x.files.map((d) => el("li", {}, [el("span", { class: "grow small", text: d.name }), P.sendPathButton(d.path, d.name, targets, said)])))]) : null]),
      el("button", { class: "link", text: "Remove", on: { click: () => act("/v1/folders/unlink", { path: x.path }, "") } })]))));
    shown = { node: t.node, said: said, lines: carried, rows: lineEls, typed: () => !!path.value.trim() };
    if (adding) lock(true);
    if (rows.some((d) => d.waiting) || f.linked.some((x) => x.waiting)) watch(3000);
    return t.node;
  }

  /* The newest files in My files (D136), each with Open: what Office files made, and anything she keeps there.
   * A folder an app marked as its own (.app-files, WB2) never shows here. */
  async function filesTile() {
    const t = P.tile({ icon: "description", title: "Made by your bot", wide: true, id: "my-files" });
    const r = await P.api("GET", "/v1/knowledge/files");
    const files = r.status === 200 && r.data && Array.isArray(r.data.files) ? r.data.files : [];
    const said = el("p", { class: "small", "data-said": "files" });
    const redraw = async () => t.node.replaceWith(await filesTile());
    if (!files.length) { t.body.appendChild(el("p", { class: "empty", text: "Nothing in My files yet. What your bot makes for you lands here." })); return t.node; }
    t.body.append(el("ul", { class: "lines", "data-documents": "files" }, files.map((f) => el("li", { "data-file": f.path }, [
      el("div", { class: "grow" }, [el("b", { text: f.name }), f.folder ? el("div", { class: "faint small", text: f.folder }) : null]),
      el("div", { class: "acts" }, [el("button", { class: "link", text: "Open", on: { click: async () => {
        said.textContent = "";
        if (L.onNetworkHost(location.hostname)) { said.textContent = await fileToDevice(f); return; }   // D130: to this device
        const x = await P.api("POST", "/v1/knowledge/files/open", { path: f.path });
        said.textContent = x.status === 200 ? "Opened on the bot's computer." : P.said(x);
      } } }), removeButton(f.name, { path: f.path }, said, redraw, "/v1/knowledge/files/remove")])]))), said);
    return t.node;
  }

  /* What the builds read (lane I11's GET /v1/knowledge/sources), grouped by source, each with its title,
   * link, license, size and date, and its source's credit (Wikipedia's CC BY-SA). */
  /* Remove asks once more on the same button, as v1's dialog did: it is deleted from this computer. */
  function removeButton(label, body, said, redraw, route) {
    let sure = false;
    const b = el("button", { class: "link", "data-remove": body.file || body.source || body.path, text: "Remove", on: { click: async () => {
      if (!sure) { sure = true; b.textContent = "Delete it from this computer?"; said.textContent = label; return; }
      said.textContent = "Removing...";
      const x = await P.api("POST", route || "/v1/knowledge/books/remove", body);
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      said.textContent = "";
      redraw();
    } } });
    return b;
  }

  /* Read a book where it is (v1's viewer, in words): a kept text in parts, or a .zim's articles by title. */
  function readText(box, source, file) {
    async function show(part) {
      const q = "source=" + encodeURIComponent(source) + "&file=" + encodeURIComponent(file) + "&part=" + part;
      const r = await P.api("GET", "/v1/knowledge/books/read?" + q);
      clear(box);
      if (r.status !== 200) { box.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      const d = r.data;
      box.append(el("h3", { text: d.title }), el("div", { class: "row small" }, [
        d.part > 0 ? el("button", { class: "link", text: "Back", on: { click: () => show(d.part - 1) } }) : null,
        el("span", { class: "faint", text: "Part " + (d.part + 1) + " of " + d.parts }),
        d.part + 1 < d.parts ? el("button", { class: "link", "data-next": "", text: "Next", on: { click: () => show(d.part + 1) } }) : null]),
        el("div", { class: "reading", "data-reading": "", text: d.text }));
    }
    show(0);
  }

  function browseLibrary(box, source, title) {
    const q = el("input", { class: "grow", placeholder: "Find an article by its title", "aria-label": "Find an article", autocomplete: "off" });
    const list = el("div");
    async function page(n) {
      const r = await P.api("GET", "/v1/knowledge/books/articles?source=" + encodeURIComponent(source) + "&q=" + encodeURIComponent(q.value.trim()) + "&page=" + n);
      clear(list);
      if (r.status !== 200) { list.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      if (!r.data.articles.length) { list.appendChild(el("p", { class: "empty", text: "No article has that in its title." })); return; }
      list.append(el("ul", { class: "lines", "data-articles": source }, r.data.articles.map((a) => el("li", {}, [el("button", { class: "link", text: a.title, on: { click: () => open(a.url) } })]))),
        el("div", { class: "row" }, [n > 0 ? el("button", { class: "link", text: "Back", on: { click: () => page(n - 1) } }) : null,
          r.data.has_more ? el("button", { class: "link", text: "More", on: { click: () => page(n + 1) } }) : null]));
    }
    async function open(url) {
      const r = await P.api("GET", "/v1/knowledge/books/article?source=" + encodeURIComponent(source) + "&url=" + encodeURIComponent(url));
      clear(list);
      list.appendChild(el("button", { class: "link", text: "Back to the articles", on: { click: () => page(0) } }));
      if (r.status !== 200) { list.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      list.append(el("h3", { text: r.data.title }), el("div", { class: "reading", "data-reading": "", text: r.data.text }));
    }
    clear(box);
    box.append(el("h3", { text: title }), el("form", { class: "row", autocomplete: "off", on: { submit: (ev) => { ev.preventDefault(); page(0); } } }, [q, el("button", { type: "submit", text: "Find" })]), list);
    page(0);
  }

  /* The whole page again when a reading ends: its list and its count. A plain redraw leaves this page as it is. */
  let wholeAgain = false;
  function drawAgain() { wholeAgain = true; P.redraw("knowledge"); }

  /* Why nothing can be looked up, with what opens it: the Bridge's own button, Toolsmith's search with its own Yes card, or
   * the way to the vault. *again* looks once more. */
  async function cantLook(box, c, again) {
    box.appendChild(el("p", { class: c.can === "no_search" ? "muted" : "fail", "data-find-cant": c.can, text: c.line }));
    const row = el("div", { class: "row" });
    const said = el("p", { class: "small" });
    const cardBox = el("div");
    if (c.can === "bridge_closed") {
      const b = await P.api("GET", "/v1/bridge");
      const btn = b.status === 200 ? await P.bridgeButton(b.data, () => { P.drawChips(); P.drawPrivate(); again(); }) : null;
      if (btn) row.appendChild(btn); else said.textContent = "To open it, use the Bridge on Settings.";
    }
    if (c.can === "no_search") row.appendChild(el("button", { type: "button", "data-find-allow": "", on: { click: async () => {
      const x = await P.api("POST", "/v1/bridge/search", { on: true });
      if (x.status !== 202 || !x.data || !x.data.consent_id) { said.textContent = P.said(x); return; }
      said.textContent = "Its Yes card names every place it may search.";
      P.consentInline(cardBox, x.data.consent_id, (answer) => { if (answer === "yes") { P.drawChips(); again(); } else said.textContent = "Toolsmith's search stays off."; });
    } } }, [P.icon("travel_explore"), "Let Toolsmith search these"]));
    if (c.can === "locked") row.appendChild(el("button", { type: "button", on: { click: () => P.go("vault") } }, [P.icon("lock_open"), "Open the Vault page"]));
    box.append(row, said, cardBox);
  }

  /* After her Yes: what is being read now, from the build's own fetch, until it ends. *done*: the page is drawn again. */
  async function watchRun(box, id, done) {
    const head = el("p", { "data-library": id }, [el("b", { text: "Starting. Nothing has been read yet." })]);
    const now = el("p", { class: "now", "data-library-now": id });
    clear(box).append(head, now);
    const ends = Date.now() + 8 * 3600 * 1000, starts = Date.now() + 3 * 60 * 1000;
    while (box.isConnected && Date.now() < ends) {
      const r = await P.api("GET", "/v1/toolsmith", undefined, L.POLL_WAIT_MS);
      if (r.status === 200) {
        const b = (r.data.builds || []).find((x) => x.id === id);
        const k = b ? L.libraryLine(b.library, b.goal, b.id) : null;
        if (k && (k.state === "done" || k.state === "short")) { done(); return; }      // short: D160, Building says what is missing
        if (k && k.fetching) { head.firstChild.textContent = k.line + "."; now.textContent = k.now; }
        else if (Date.now() > starts) { head.firstChild.textContent = "It did not start. Building says why."; return; }
      } else if (L.pollCanRetry(r.status)) now.textContent = L.POLL_RETRY;
      else { now.textContent = P.said(r); return; }
      await P.sleep(2500);
    }
  }

  /* Ask for a plan of books, follow it to its card, and say what happened. Nothing downloads before her Yes on it. */
  async function plan(box, path, body, words, onYes) {
    clear(box);
    const look = await P.api("GET", "/v1/knowledge/find");
    const c = look.status === 200 ? look.data : null;
    const again = () => plan(box, path, body, words, onYes);
    if (c && ["bridge_closed", "paused", "locked"].includes(c.can)) { await cantLook(box, c, again); return; }
    const places = c && !body ? [] : (c ? c.ask : []);
    const live = el("p", { class: "now", "data-find-now": "", text: places.length ? "Looking in " + L.andList(places) + " for \"" + words + "\"." : "Getting your plan ready." });
    box.appendChild(live);
    const started = Date.now();
    const r = await P.api("POST", path, body || {});
    if (r.status !== 201) {
      clear(box);
      if (r.data && ["bridge_closed", "paused", "locked"].includes(r.data.code)) await cantLook(box, { can: r.data.code, line: r.data.error }, again);
      else box.appendChild(el("p", { class: "fail", text: P.said(r) }));
      return;
    }
    let v = L.buildView(r.data);
    const ends = started + 11 * 60 * 1000;             // the Core gives a plan ten minutes (timing.plan_wait_s), then says so
    while (v.planning) {
      if (!box.isConnected) return;
      live.textContent = L.findLive(v.now || live.dataset.first || live.textContent, v.nowAt, started, Date.now());
      live.dataset.first = live.dataset.first || live.textContent.replace(/, \d+ seconds? so far\.$/, "");
      await P.sleep(1000);
      if (Date.now() > ends) { clear(box).appendChild(el("p", { class: "fail", "data-find-late": "", text: "Toolsmith did not finish looking in time. Nothing was downloaded. Try again." })); return; }
      const x = await P.api("GET", "/v1/toolsmith/builds/" + encodeURIComponent(v.id), undefined, L.POLL_WAIT_MS);
      if (x.status === 200) v = L.buildView(x.data);
      else if (!L.pollCanRetry(x.status)) { clear(box).appendChild(el("p", { class: "fail", text: P.said(x) })); return; }
      else { live.textContent = L.POLL_RETRY; await P.sleep(1500); }
    }
    if (!box.isConnected) return;
    clear(box);
    if (v.yes) { onYes(v.id); return; }
    if (!v.consentId) { box.appendChild(el("p", { class: "fail", "data-find-nothing": "", text: v.said || "Nothing was looked up. Try other words." })); return; }
    if (c && body && c.can === "no_search") await cantLook(box, c, again);
    if (v.looked && v.looked.line) box.appendChild(el("p", { "data-find-found": "", text: v.looked.line }));
    P.planCard(box, v, (answer) => {
      if (answer === "yes") onYes(v.id);
      else box.appendChild(el("p", { class: "small muted", text: "Nothing was downloaded." }));
    });
  }

  /* D148: what a moved bot came with, by topic: what it would fetch, and one button. One plan card, one Yes. */
  function readingRow(d) {
    const box = el("div", { "data-reading-box": "" });
    const get = () => plan(box, "/v1/knowledge/recommended/reading", null, "", (id) => watchRun(box, id, drawAgain));
    const what = d.sources.map((s) => s.name + ": up to " + s.up_to + " " + s.kind + ".").join(" ") + " About " + d.about_mb + " MB in all, kept on this computer.";
    const parts = [el("b", { text: "Books and articles for this bot" }),
      el("ul", { class: "small", "data-reading-topics": "" }, d.topics.map((x) => el("li", { text: x })))];
    if (d.state === "done") parts.push(el("p", { class: "small", "data-reading-done": "", text: d.line + " They are in Your library, below." }));
    else parts.push(el("p", { class: "small muted", "data-reading-what": "", text: what }));
    if (d.state === "offer" && d.line) parts.push(el("p", { class: "small", text: "Last time: " + d.line }));
    parts.push(box);
    if (d.state === "offer") box.appendChild(el("button", { class: "primary", type: "button", "data-reading-get": "", on: { click: get } }, [P.icon("download"), "Get these"]));
    if (d.state === "asking") get();
    if (d.state === "fetching") watchRun(box, d.build, drawAgain);
    return el("li", { "data-reading": d.state }, [el("div", { class: "grow" }, parts)]);
  }

  /* D148: what a moved bot came with. She picks: each goes by its normal path and its normal Yes, and nothing comes by itself. */
  async function recommendedTile() {
    const r = await P.api("GET", "/v1/knowledge/recommended");
    const rows = r.status === 200 && Array.isArray(r.data.rows) ? r.data.rows : [];
    const reading = r.status === 200 ? r.data.reading : null;
    if (!rows.length && !reading) return null;
    const t = P.tile({ icon: "menu_book", title: "Recommended for this bot", wide: true, id: "recommended" });
    const redraw = async () => { const next = await recommendedTile(); if (next) t.node.replaceWith(next); else t.node.remove(); };
    function row(d) {
      const box = el("div", { "data-recommended-box": d.id });
      async function get() {
        if (d.get === "find") { await plan(box, "/v1/knowledge/find", { words: d.name }, d.name, (id) => watchRun(box, id, drawAgain)); return; }
        clear(box).appendChild(el("p", { class: "now", text: "Getting it ready to ask you..." }));
        const x = await P.api("POST", "/v1/packages", { source: d.source });
        clear(box);
        if (x.status !== 202 || !x.data.consent_id) box.appendChild(el("p", { class: "fail", text: P.said(x) }));
        else P.consentInline(box, x.data.consent_id, redraw);
      }
      return el("li", { "data-recommended": d.id }, [
        el("div", { class: "grow" }, [el("b", { text: d.name }), el("div", { class: "faint small", text: d.line || (d.kind === "book" ? "A book" : "An app") }), box]),
        d.get === "ask" ? null : el("button", { class: "link", "data-recommended-get": d.id, text: d.get === "find" ? "Find it" : "Add", on: { click: get } })]);
    }
    t.body.append(el("p", { class: "small muted", text: "What this bot had on its last computer. Pick what you want back. Nothing downloads until you say Yes." }),
      el("ul", { class: "lines", "data-recommended-list": "" }, (reading ? [readingRow(reading)] : []).concat(rows.map(row))));
    return t.node;
  }

  /* Words from Apps, Add something new: what she typed there was about books, so it is looked up here (see apps.js). */
  let asked = "", askedTimes = 0;
  P.askBooks = (words) => { asked = String(words || ""); askedTimes += 1; P.go("knowledge", String(askedTimes)); };

  /* Find more books (v1's Find more knowledge): Toolsmith's plan card, books only, nothing until her Yes. It says where it
   * looks and what it found (sources.json): the catalogs while the plan is made, and the rest only after her Yes. */
  function findBooks(box, onYes) {
    const q = el("input", { class: "grow", placeholder: "A subject, or a book's title", "aria-label": "What to find books about", autocomplete: "off" });
    const out = el("div", { "data-find-books": "" });
    const go = el("button", { type: "submit" }, [P.icon("search"), "Find books"]);
    async function look() {
      const words = q.value.trim();
      if (!words) return;
      go.disabled = true;
      await plan(out, "/v1/knowledge/find", { words }, words, onYes);
      go.disabled = false;
    }
    const fill = (words) => { q.value = words; look(); };      // D161: a search that found nothing offers it with her words
    box.append(el("form", { class: "row", autocomplete: "off", on: { submit: (ev) => { ev.preventDefault(); look(); } } }, [q, go]),
      el("p", { class: "small muted", "data-find-what": "", text: "These libraries hold free books in the public domain and reference articles, not new novels. Nothing downloads before your Yes." }), out);
    if (asked) { const words = asked; asked = ""; fill(words); return fill; }
    P.api("GET", "/v1/knowledge/find").then((r) => {
      if (r.status === 200 && r.data.can !== "ok" && !out.childElementCount) cantLook(out, r.data, drawAgain);
    });
    return fill;
  }

  async function sourcesTile() {
    const t = P.tile({ icon: "menu_book", title: "Your library", wide: true, id: "sources" });
    const redraw = async () => t.node.replaceWith(await sourcesTile());
    const said = el("p", { class: "small", "data-said": "books" });
    const reader = el("div", { "data-reader": "" });
    const [r, tool] = await Promise.all([P.api("GET", "/v1/knowledge/sources"), P.api("GET", "/v1/toolsmith")]);
    const lib = tool.status === 200 ? L.libraryNow(tool.data) : null;     // lane I19b: fetching now, said plainly
    library = { node: t.node, seen: r.status === 200 ? r.data.count : 0, fetching: !!(lib && lib.fetching), reader: reader,
                busy: () => !!said.textContent };
    if (library.fetching) watch(3000);
    if (lib) [el("p", { "data-library": lib.id }, [el("b", { text: lib.line + "." })]), lib.now ? el("p", { class: "now", text: lib.now }) : null,
      lib.end ? el("p", { class: "small", "data-library-end": lib.id, text: lib.end }) : null]
      .forEach((n) => n && t.body.appendChild(n));
    if (P.missing(r)) { t.body.appendChild(P.later("the list of what your builds read (GET /v1/knowledge/sources)")); return t.node; }
    if (r.status !== 200) { t.body.appendChild(el("p", { class: "fail", text: P.said(r) })); return t.node; }
    const k = L.knowledgeGroups(r.data);
    const libs = Array.isArray(r.data.libraries) ? r.data.libraries : [];
    const left = Array.isArray(r.data.waiting) ? r.data.waiting : [];
    const dropTo = el("div", { class: "small", "data-drop": "" }, [
      el("p", { class: "muted", text: "Have a .zim file? Put it in this folder and your bot learns it by itself:" }), el("code", { text: String(r.data.drop_folder || "") }),
      el("button", { class: "link", text: "Look for new ones now", on: { click: async () => { await P.api("POST", "/v1/knowledge/books/look", {}); redraw(); } } })]
      .concat(left.map((w) => el("p", { "data-waiting": w.name, text: w.name + ": " + w.line }))));
    if (k.empty && !lib && !libs.length) { t.body.append(el("p", { class: "empty", "data-said": "sources-empty", text: L.KNOWLEDGE_EMPTY }), dropTo, said); return t.node; }
    t.body.appendChild(el("p", { class: "small muted", "data-said": "library-here", text: lib ? lib.note : L.LIBRARY_DONE }));
    if (!k.empty) t.head.appendChild(el("span", { class: "stat", text: k.count + (k.count === 1 ? " ITEM, " : " ITEMS, ") + k.size.toUpperCase() }));
    // A whole library: one row, Browse and Remove. A .zim she dropped has only this row; a download's articles follow it.
    if (libs.length) t.body.appendChild(el("ul", { class: "lines", "data-libraries": "" }, libs.map((b) => el("li", { "data-library-row": b.source }, [
      el("div", { class: "grow" }, [el("b", { text: b.title }), el("div", { class: "faint small", text: "On this computer, " + L.sizeWords(b.size) }), b.line ? el("div", { class: "small", text: b.line }) : null]),
      el("div", { class: "acts" }, [b.zim ? el("button", { class: "link", "data-browse": b.source, text: "Browse", on: { click: () => browseLibrary(reader, b.source, b.title) } }) : null,
        removeButton(b.title, { source: b.source }, said, redraw)])]))));
    k.groups.forEach((g) => {
      const whole = libs.find((b) => b.source === g.source);
      t.body.append(el("h3", { class: "micro", text: (whole ? whole.title : g.name) + " (" + g.count + ", " + g.size + ")" }),
        g.credit ? el("p", { class: "faint small", text: g.credit }) : null,
        el("ul", { class: "lines", "data-source": g.source }, g.items.map((it) => el("li", { "data-book": it.file }, [el("div", { class: "grow" }, [
          el("b", { text: it.title }),
          el("div", { class: "faint small", text: ["On this computer", it.license, it.size, it.date].filter(Boolean).join(", ") }),
          // the source, labeled as such: it opens the website in a new tab of her real browser (lane I19b)
          it.url ? el("div", { class: "small" }, [el("a", { href: it.url, target: "_blank", rel: "noopener noreferrer", title: L.SOURCE_LINK_NOTE,
            "data-source-link": "", text: "Source: " + it.host }), el("span", { class: "faint", text: " (a new tab)" })]) : null]),
          it.file ? el("div", { class: "acts" }, [el("button", { class: "link", "data-read": it.file, text: "Read", on: { click: () => readText(reader, g.source, it.file) } }),
            removeButton(it.title, { source: g.source, file: it.file }, said, redraw)]) : null]))));
    });
    t.body.append(dropTo, said, reader);
    return t.node;
  }

  /* D159: is it indexing now, how far along, and when it last finished. It asks again by itself while it is on the page. */
  function indexTile() {
    const t = P.tile({ icon: "manage_search", title: "Indexing", wide: true, id: "indexing" });
    const box = el("div", { "data-index": "" });
    t.body.appendChild(box);
    async function look() {
      const r = await P.api("GET", "/v1/knowledge/index", undefined, L.POLL_WAIT_MS);
      const v = r.status === 200 ? L.indexNow(r.data, Date.now()) : null;
      if (v) clear(box).append(el("p", { "data-index-now": v.running ? "running" : "idle" }, [el("b", { text: v.line })]),
        ...v.more.map((line) => el("p", { class: "small muted", text: line })));
      else if (!box.childElementCount) box.appendChild(el("p", { class: "muted", text: P.said(r, "BuildBot did not say how indexing is going.") }));
      setTimeout(() => { if (t.node.isConnected) look(); }, v && v.running ? 3000 : 20000);
    }
    look();
    return t.node;
  }

  P.page("knowledge", {
    title: "Knowledge",
    async draw(sec, arg, live) {
      if (live && !wholeAgain) { watch(1000); return; }      // the Core said something changed: look soon
      wholeAgain = false;                                     // a reading ended: the whole page again (drawAgain)
      const search = searchTile();
      const box = el("div", { class: "tiles" }, [indexTile(), search]);
      clear(sec).appendChild(box);
      const [moved, , files, lib] = await Promise.all([recommendedTile(), documentsTile(), filesTile(), sourcesTile()]);
      box.insertBefore(shown.node, search);       // the newest: a batch may have ended while this waited on another tile
      if (shown.lines === carried) carried = [];  // she has them now
      if (moved) box.appendChild(moved);
      box.append(files, P.knowledgePlaceTile(), lib);
      if (!watching) watch(15000);
    },
  });
})();
