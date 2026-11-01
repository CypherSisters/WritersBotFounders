/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/knowledge/page.tsx, knowledge/MyData.tsx and
 * knowledge/BuildSources.tsx (adapted). Knowledge: search what is indexed on this computer (the search_knowledge
 * tool, POST /v1/chat/tool), her documents (pin a file, link a folder, D125 and D126), and what your builds read (lane
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
    q.classList.add("grow");
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (!q.value.trim()) return;
      clear(out).appendChild(el("p", { class: "muted", text: "Searching..." }));
      const r = await P.api("POST", "/v1/chat/tool", { tool: "search_knowledge", args: { query: q.value.trim(), limit: 10 } });
      clear(out);
      if (r.status !== 200) { out.appendChild(el("p", { class: "fail", text: P.said(r, "The search did not run.") })); return; }
      const list = hits(r.data);
      if (!list.length) { out.appendChild(el("p", { class: "empty", text: "Nothing found. Only your documents below and your bot's library are searched." })); return; }
      out.appendChild(el("ul", { class: "lines" }, list.map((h) => el("li", {}, [el("div", {}, [el("b", { text: h.title }),
        h.where && h.where !== h.title ? el("div", { class: "faint small", text: h.where }) : null, h.text ? el("div", { class: "small", text: h.text }) : null])]))));
    });
    t.body.append(f, out);
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

  let carried = "";     // a line that outlives the tile being drawn again (D134: what an upload did)

  /* Her documents, in one obvious place (D125, D126): one file pinned to the bot, for every chat, and whole folders
   * linked, each with what it read and what it couldn't. Developer's Vault page has the same folders. */
  function adder(kind, choose, label, placeholder, button, icon, check, said, send, uploaded) {
    const here = !L.onNetworkHost(location.hostname);
    const fromDevice = uploaded ? P.uploadInput(said, uploaded) : null;    // D134: a file on the device she is at
    const path = el("input", { class: "grow", type: "text", placeholder: placeholder, autocomplete: "off", spellcheck: "false", "aria-label": label });
    const go = el("button", { on: { click: () => {
      const p = check(path.value);
      if (p.ok) send(p.path); else said.textContent = p.line;
    } } }, [button]);
    const typed = el("details", {}, [el("summary", { class: "small faint", text: here ? "Or type its path" : "Or type a path on your bot" }), el("div", { class: "row" }, [path, go])]);
    const pick = el("button", { class: "primary", "data-pick": kind, on: { click: async () => {
      if (!here && fromDevice) { fromDevice.click(); return; }    // at once: a browser opens its picker only on a click
      if (!here) { said.textContent = FOLDER_FROM_DEVICE; typed.open = true; return; }
      const got = await P.pickPath(kind, said);
      if (got && got.upload && fromDevice) { fromDevice.hidden = false; fromDevice.click(); return; }   // shown too, if it won't open by itself
      if (got && !got.upload) send(got); else if (said.textContent) typed.open = true;
    } } }, [P.icon(icon), choose]);
    return el("div", {}, [el("div", { class: "row" }, [pick, fromDevice]), typed]);
  }

  async function documentsTile() {
    const t = P.tile({ icon: "folder_open", title: "Your documents", id: "folders", wide: true });
    const [pins, folders, targets] = await Promise.all([P.api("GET", "/v1/knowledge/pinned"), P.api("GET", "/v1/folders"), P.sendTargets()]);
    const pinned = pins.status === 200 && pins.data && Array.isArray(pins.data.pinned) ? pins.data.pinned : [];
    const f = D.foldersView(folders.status === 200 ? folders.data : {});
    const said = el("p", { class: "small", "data-said": "documents", text: carried });
    carried = "";
    t.head.appendChild(el("span", { class: "stat", text: pinned.length + " PINNED, " + f.linked.length + " LINKED" }));

    async function act(path, body, busy, line) {
      said.textContent = busy;
      const x = await P.api("POST", path, body);
      if (x.status !== 200) { said.textContent = P.said(x); return; }
      carried = line || "";
      t.node.replaceWith(await documentsTile());
    }
    /* D134: a sent file is pinned, as a picked one is. A new copy of a pinned file is read again by the Core: nothing to pin. */
    async function uploaded(d) {
      if (!d.pinned) { await act("/v1/knowledge/pinned", { path: d.path }, "Reading it through your privacy check...", d.line); return; }
      carried = d.line || "";
      t.node.replaceWith(await documentsTile());
    }

    t.body.append(el("h3", { class: "micro", text: "Pinned documents" }),
      el("p", { class: "small muted", text: "One file your bot knows by name in every chat, so you never link it again. It is read where it is and never changed." }));
    if (pinned.length) t.body.appendChild(el("ul", { class: "lines", "data-documents": "pinned" }, pinned.map((d) => el("li", { "data-pinned": d.path }, [
      el("div", { class: "grow" }, [el("b", { text: d.name }), el("div", { class: "faint small", text: d.path }), el("div", { class: "small", text: d.line })]),
      el("div", { class: "acts" }, [P.sendPathButton(d.path, d.name, targets, said), el("button", { class: "link", text: "Unpin", on: { click: () => act("/v1/knowledge/pinned/remove", { path: d.path }, "") } })])]))));
    t.body.append(adder("file", "Pick a file", "A file to pin", "C:\\Users\\you\\Documents\\My Novel.docx", "Pin this file", "keep", D.filePath, said,
      (p) => act("/v1/knowledge/pinned", { path: p }, "Reading it through your privacy check..."), uploaded),
      el("p", { class: "faint small", text: "It can be a .txt, .md, .docx or .pdf file. A file sent from this device can be up to 50 MB. To bring back your changes, send it again." }),
      el("h3", { class: "micro", text: "Linked folders" }),
      el("p", { class: "small muted", text: "Every document in a folder, read where it is, never copied." }));
    if (f.linked.length) t.body.appendChild(el("ul", { class: "lines", "data-documents": "linked" }, f.linked.map((x) => el("li", { "data-linked": x.path }, [
      el("div", { class: "grow" }, [el("code", { text: x.path }), el("div", { class: "small", text: x.line }),
        targets.length && x.files.length ? el("details", { "data-folder-files": x.path }, [el("summary", { class: "small", text: "See its files, and send one to an app" }),
          el("ul", { class: "lines" }, x.files.map((d) => el("li", {}, [el("span", { class: "grow small", text: d.name }), P.sendPathButton(d.path, d.name, targets, said)])))]) : null]),
      el("button", { class: "link", text: "Unlink", on: { click: () => act("/v1/folders/unlink", { path: x.path }, "") } })]))));
    t.body.append(adder("folder", "Pick a folder", "A folder to link", "C:\\Users\\you\\Documents\\Recipes", "Link this folder", "add_link", D.folderPath, said,
      (p) => act("/v1/folders/link", { path: p }, "Linking it...")), said);
    return t.node;
  }

  /* The newest files in My files (D136), each with Open: what Office files made, and anything she keeps there. */
  async function filesTile() {
    const t = P.tile({ icon: "description", title: "Your files", wide: true, id: "my-files" });
    const r = await P.api("GET", "/v1/knowledge/files");
    const files = r.status === 200 && r.data && Array.isArray(r.data.files) ? r.data.files : [];
    const said = el("p", { class: "small", "data-said": "files" });
    if (!files.length) { t.body.appendChild(el("p", { class: "empty", text: "Nothing in My files yet. What your bot makes for you lands here." })); return t.node; }
    t.body.append(el("ul", { class: "lines", "data-documents": "files" }, files.map((f) => el("li", { "data-file": f.path }, [
      el("div", { class: "grow" }, [el("b", { text: f.name }), f.folder ? el("div", { class: "faint small", text: f.folder }) : null]),
      el("button", { class: "link", text: "Open", on: { click: async () => {
        said.textContent = "";
        if (L.onNetworkHost(location.hostname)) { said.textContent = await fileToDevice(f); return; }   // D130: to this device
        const x = await P.api("POST", "/v1/knowledge/files/open", { path: f.path });
        said.textContent = x.status === 200 ? "Opened on the bot's computer." : P.said(x);
      } } })]))), said);
    return t.node;
  }

  /* What the builds read (lane I11's GET /v1/knowledge/sources), grouped by source, each with its title,
   * link, license, size and date, and its source's credit (Wikipedia's CC BY-SA). */
  /* Remove asks once more on the same button, as v1's dialog did: it is deleted from this computer. */
  function removeButton(label, body, said, redraw) {
    let sure = false;
    const b = el("button", { class: "link", "data-remove": body.file || body.source, text: "Remove", on: { click: async () => {
      if (!sure) { sure = true; b.textContent = "Delete it from this computer?"; said.textContent = label; return; }
      said.textContent = "Removing...";
      const x = await P.api("POST", "/v1/knowledge/books/remove", body);
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

  /* Find more books (v1's Find more knowledge): Toolsmith's plan card, books only, nothing until her Yes. */
  function findBooks(box) {
    const q = el("input", { class: "grow", placeholder: "What should it know about?", "aria-label": "What to find books about", autocomplete: "off" });
    const out = el("div", { "data-find-books": "" });
    const go = el("button", { class: "primary", type: "submit" }, [P.icon("search"), "Find books"]);
    box.append(el("form", { class: "row", autocomplete: "off", on: { submit: async (ev) => {
      ev.preventDefault();
      if (!q.value.trim()) return;
      go.disabled = true;
      clear(out).appendChild(el("p", { class: "now", text: "Looking through the libraries. This can take a minute." }));
      const r = await P.api("POST", "/v1/knowledge/find", { words: q.value.trim() });
      go.disabled = false;
      clear(out);
      if (r.status !== 201) { out.appendChild(el("p", { class: "fail", text: P.said(r) })); return; }
      P.planCard(out, L.buildView(r.data));
    } } }, [q, go]), out);
  }

  async function sourcesTile() {
    const t = P.tile({ icon: "menu_book", title: "Your library", wide: true, id: "sources" });
    const redraw = async () => t.node.replaceWith(await sourcesTile());
    const said = el("p", { class: "small", "data-said": "books" });
    const reader = el("div", { "data-reader": "" });
    findBooks(t.body);
    const [r, tool] = await Promise.all([P.api("GET", "/v1/knowledge/sources"), P.api("GET", "/v1/toolsmith")]);
    const lib = tool.status === 200 ? L.libraryNow(tool.data) : null;     // lane I19b: fetching now, said plainly
    if (lib) [el("p", { "data-library": lib.id }, [el("b", { text: lib.line + "." })]), lib.now ? el("p", { class: "now", text: lib.now }) : null]
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
    if (!k.empty) t.head.appendChild(el("span", { class: "stat", text: k.count + " READ, " + k.size.toUpperCase() }));
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

  P.page("knowledge", {
    title: "Knowledge",
    async draw(sec, arg, live) {
      if (live) return;
      const box = el("div", { class: "tiles" }, [searchTile()]);
      clear(sec).appendChild(box);
      const [docs, files, lib] = await Promise.all([documentsTile(), filesTile(), sourcesTile()]);
      box.append(docs, files, P.knowledgePlaceTile(), lib);
    },
  });
})();
