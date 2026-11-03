// V3-1: Add documents, the words and the real page code (pages/knowledge.js, sendfile.js) on a small stand-in for the page
// and a fake Core. No browser and no real Core: the clicks here are calls. Prints "ok".
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");
const L = require(path.join(web, "logic.js"));
const D = require(path.join(web, "devzones.js"));

// -- a small stand-in for the page ----------------------------------------------------------------
class Node {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; this.text = ""; this.parent = null; this.value = ""; this.checked = false; this.disabled = false; this.open = false; this.className = ""; this.files = []; this.clicks = 0; this.classList = { add: (c) => { this.className += " " + c; } }; }
  get textContent() { return this.text + this.kids.map((k) => k.textContent).join(" "); }
  set textContent(v) { this.text = String(v); this.kids.forEach((k) => { k.parent = null; }); this.kids = []; }
  get hidden() { return "hidden" in this.attrs; }
  set hidden(v) { if (v) this.attrs.hidden = "hidden"; else delete this.attrs.hidden; }
  append(...k) { k.forEach((c) => c && this.appendChild(typeof c === "string" ? new Text(c) : c)); }
  appendChild(c) { if (c.parent) c.parent.kids.splice(c.parent.kids.indexOf(c), 1); c.parent = this; this.kids.push(c); return c; }
  insertBefore(c, before) { if (c.parent) c.parent.kids.splice(c.parent.kids.indexOf(c), 1); c.parent = this; this.kids.splice(this.kids.indexOf(before), 0, c); }
  replaceWith(n) { if (!this.parent) return; const p = this.parent; p.kids[p.kids.indexOf(this)] = n; n.parent = p; this.parent = null; }
  remove() { if (this.parent) { this.parent.kids.splice(this.parent.kids.indexOf(this), 1); this.parent = null; } }
  addEventListener(ev, fn) { this.on[ev] = fn; }
  click() { if (this.disabled) return; this.clicks++; return this.on.click && this.on.click(); }
  all(test, out) { out = out || []; this.kids.forEach((k) => { if (test(k)) out.push(k); k.all(test, out); }); return out; }
  find(attr, value) { return this.all((n) => attr in n.attrs && (value === undefined || n.attrs[attr] === value))[0]; }
  querySelectorAll(sel) { assert.strictEqual(sel, "button, input"); return this.all((n) => n.tag === "button" || n.tag === "input"); }
}
class Text extends Node { constructor(t) { super("#text"); this.text = t; } }

function el(tag, attrs, kids) {
  const n = new Node(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "text") n.text = String(v);
    else if (k === "on") Object.assign(n.on, v);
    else if (k === "class") n.className = v;
    else n.attrs[k] = v === true ? "" : String(v);
  });
  (kids || []).forEach((c) => c && n.appendChild(typeof c === "string" ? new Text(c) : c));
  return n;
}
const clear = (n) => { n.textContent = ""; return n; };
function tile(o) {
  const body = el("div"), head = el("div", {}, [el("h2", { text: o.title })]);
  return { node: el("section", { "data-tile": o.id || null }, [head, body]), body, head };
}

// -- a fake Core ----------------------------------------------------------------------------------
const said = (r, fallback) => (r.data && r.data.error) || fallback || "That did not work.";
let calls, reply, docs, linked, held;
function reset() { calls = []; reply = {}; docs = []; linked = []; held = null; }
async function api(method, url, body) {
  if (body && !("size" in body)) body = JSON.parse(JSON.stringify(body));     // plain, from the page's own context
  calls.push([method, url.split("?")[0], body]);
  const key = method + " " + url.split("?")[0];
  if (reply[key]) { const r = reply[key](body, url); if (r) return r; }
  if (held && held.key === key) await new Promise((go) => { held.go = go; });
  if (key === "GET /v1/knowledge/documents") return { status: 200, data: { documents: docs } };
  if (key === "GET /v1/folders") return { status: 200, data: { linked: linked } };
  if (key === "POST /v1/knowledge/documents") return { status: 200, data: { said: L.fileNameOf(body.path) + " is ready. Your bot can find it now." } };
  if (key === "POST /v1/knowledge/upload") return { status: 200, data: { said: decodeURIComponent(url.split("name=")[1]) + " is ready." } };
  if (key === "POST /v1/folders/link") { linked.push({ path: body.path, there: true }); return { status: 200, data: {} }; }
  return { status: 404, data: { error: "no" } };
}

const defs = {};
const P = { el, clear, L, tile, api, said, icon: (n) => el("span", { text: "" }), page: (id, d) => { defs[id] = d; }, missing: () => false, later: () => el("p"),
  knowledgePlaceTile: () => el("section", { "data-tile": "place" }), authHeaders: (h) => h };
const box = { window: { Puff: P, PuffDev: D }, location: { hostname: "puffpai.localhost" }, console, setTimeout: (f, ms) => { const t = setTimeout(f, ms); t.unref(); return t; }, clearTimeout, URL, Array, Promise, JSON, encodeURIComponent, decodeURIComponent };
vm.createContext(box);
for (const f of ["sendfile.js", "pages/knowledge.js"]) vm.runInContext(fs.readFileSync(path.join(web, f), "utf8"), box, { filename: f });

const settle = async () => { for (let i = 0; i < 40; i++) await new Promise((go) => setImmediate(go)); };
async function open(hostname) {
  box.location.hostname = hostname || "puffpai.localhost";
  const sec = el("div");
  await defs.knowledge.draw(sec);
  return sec;
}
const tileOf = (sec) => sec.find("data-tile", "folders");
const posts = (url) => calls.filter((c) => c[0] === "POST" && c[1] === url).map((c) => c[2]);
const saidIn = (sec) => tileOf(sec).find("data-said", "documents");
const lineTexts = (sec) => saidIn(sec).kids.map((k) => k.text);
const file = (name, size) => ({ name, size: size === undefined ? 5 : size });
const add = (sec) => tileOf(sec).find("data-add", "documents");
const folder = (sec) => tileOf(sec).find("data-add", "folder");
const picker = (sec) => tileOf(sec).find("data-upload");
function choose(input, files) { input.files = files; const on = input.on.change; Object.defineProperty(input, "value", { configurable: true, get: () => "", set: () => { input.files = []; } }); on(); }

(async () => {
  // -- the words ----------------------------------------------------------------------------------
  assert.strictEqual(L.documentsHead(3, 1), "3 DOCUMENTS, 1 FOLDER");
  assert.strictEqual(L.documentsHead(1, 2), "1 DOCUMENT, 2 FOLDERS");
  assert.strictEqual(L.documentsHead(0, 0), "0 DOCUMENTS, 0 FOLDERS");
  assert.strictEqual(L.fileNameOf("C:\\a b\\novel.docx"), "novel.docx");
  assert.strictEqual(L.fileNameOf("/home/me/novel.docx"), "novel.docx");
  const [named, copy] = L.documentRows([{ name: "A", path: "C:\\A", line: "Ready.", ok: true, named: true }, { name: "B", path: "C:\\up\\B", copy: true }, null, { name: "no path" }]).concat([]);
  assert.strictEqual(L.documentRows([null, { name: "no path" }]).length, 0);
  assert.strictEqual(L.documentRows("nope").length, 0);
  assert.strictEqual(named.copyNote, ""); assert.strictEqual(named.ok, true);
  assert.strictEqual(copy.copyNote, "A copy on your bot. To bring in changes, send it again.");
  assert.strictEqual(copy.ok, false);

  // one that fails, or throws, never stops the next; the Core's words are not changed
  const going = [];
  const lines = await L.addInTurn([{ name: "a.docx" }, { name: "b.doc" }, { name: "c.pdf" }, { name: "d.md" }], async (d) => {
    if (d.name === "b.doc") return { error: "That kind can't be read." };
    if (d.name === "c.pdf") throw new Error("gone");
    return { said: d.name + " is ready." };
  }, (l) => going.push(l));
  assert.deepStrictEqual(going, ["Reading 1 of 4: a.docx", "Reading 2 of 4: b.doc", "Reading 3 of 4: c.pdf", "Reading 4 of 4: d.md"]);
  assert.deepStrictEqual(lines, [{ text: "a.docx is ready.", fail: false }, { text: "b.doc: That kind can't be read.", fail: true },
    { text: "c.pdf: That did not work.", fail: true }, { text: "d.md is ready.", fail: false }]);

  // -- the page: the order, the head, the empty list ---------------------------------------------
  reset();
  let sec = await open();
  const order = sec.kids[0].kids.map((n) => n.attrs["data-tile"] || n.kids[0].kids[0].text);
  assert.deepStrictEqual(order, ["folders", "Search your knowledge", "my-files", "place", "sources"]);
  assert.strictEqual(sec.find("data-tile", "my-files").kids[0].kids[0].text, "Made by your bot");
  assert.ok(tileOf(sec).textContent.includes("0 DOCUMENTS, 0 FOLDERS"));
  assert.ok(tileOf(sec).textContent.includes("Nothing here yet. Add a document and your bot can find it."));
  assert.ok(tileOf(sec).textContent.includes("It can be a .txt, .md, .docx or .pdf file. A file sent from a device can be up to 50 MB."));
  assert.ok(!tileOf(sec).textContent.includes("Folders"));
  assert.strictEqual(add(sec).kids[1].text, "Add documents");
  assert.strictEqual(folder(sec).text, "Add a whole folder");
  assert.strictEqual(picker(sec).attrs.multiple, "multiple");
  assert.strictEqual(tileOf(sec).find("data-folder-typed").hidden, true);
  // what she sees sits directly under the button's row
  const body = tileOf(sec).kids[1];
  assert.strictEqual(body.kids[0].kids.includes(add(sec)), true);
  assert.strictEqual(body.kids[1], saidIn(sec));

  // -- the bot's computer: the dialog, many files, one refused --------------------------------------
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { paths: ["C:\\a\\one.docx", "C:\\a\\old.doc", "C:\\a\\two.pdf"] } });
  reply["POST /v1/knowledge/documents"] = (b) => (b.path.endsWith("old.doc") ? { status: 400, data: { error: "PuffPAI can't read that kind." } } : null);
  held = { key: "POST /v1/knowledge/documents" };
  const first = tileOf(sec);
  add(sec).click();
  assert.strictEqual(saidIn(sec).text, "Look for the window that just opened on this computer.");
  assert.strictEqual(add(sec).disabled, true);
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/pick"), [{ kind: "files" }]);
  assert.strictEqual(saidIn(sec).text, "Reading 1 of 3: one.docx");
  // while it runs: nothing starts a second batch, and the tile is not drawn again
  assert.strictEqual(add(sec).disabled, true);
  assert.strictEqual(folder(sec).disabled, true);
  add(sec).click();
  assert.strictEqual(posts("/v1/knowledge/pick").length, 1);
  assert.strictEqual(tileOf(sec), first);
  held.key = "none"; held.go();
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/documents").map((b) => b.path), ["C:\\a\\one.docx", "C:\\a\\old.doc", "C:\\a\\two.pdf"]);
  assert.notStrictEqual(tileOf(sec), first);                     // drawn again, once, after the last
  assert.deepStrictEqual(lineTexts(sec), ["one.docx is ready. Your bot can find it now.", "old.doc: PuffPAI can't read that kind.", "two.pdf is ready. Your bot can find it now."]);
  assert.deepStrictEqual(saidIn(sec).kids.map((k) => k.className), ["", "fail warn-t", ""]);
  assert.strictEqual(add(sec).disabled, false);
  assert.deepStrictEqual(posts("/v1/knowledge/pinned"), []);     // the page never pins

  // the page drawn again while a batch runs: still one batch, and the lines reach the new tile
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { paths: ["C:\\a\\one.docx", "C:\\a\\two.pdf"] } });
  held = { key: "POST /v1/knowledge/documents" };
  add(sec).click();
  await settle();
  const again = await open();
  assert.strictEqual(add(again).disabled, true);
  held.go();
  await settle();
  assert.strictEqual(saidIn(again).text, "Reading 2 of 2: two.pdf");
  held.key = "none"; held.go();
  await settle();
  assert.deepStrictEqual(lineTexts(again), ["one.docx is ready. Your bot can find it now.", "two.pdf is ready. Your bot can find it now."]);
  assert.strictEqual(add(again).disabled, false);

  // a closed dialog says nothing and changes nothing
  reset();
  sec = await open();
  const same = tileOf(sec);
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { paths: [] } });
  add(sec).click();
  await settle();
  assert.strictEqual(tileOf(sec), same);
  assert.strictEqual(saidIn(sec).textContent, "");
  assert.strictEqual(add(sec).disabled, false);
  assert.deepStrictEqual(posts("/v1/knowledge/documents"), []);
  assert.strictEqual(picker(sec).hidden, true);
  assert.strictEqual(picker(sec).clicks, 0);

  // the dialog can't open (409): the Core's words, then the browser's input, shown and clicked
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 409, data: { error: "A file window is already open on this computer." } });
  add(sec).click();
  await settle();
  assert.strictEqual(saidIn(sec).text, "A file window is already open on this computer.");
  assert.strictEqual(picker(sec).hidden, false);
  assert.strictEqual(picker(sec).clicks, 1);
  assert.strictEqual(picker(sec).disabled, false);

  // no dialog on this bot: the Core's upload answer goes the same way
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { paths: [], typed: true, upload: true, line: "Choose the file from this device." } });
  add(sec).click();
  await settle();
  assert.strictEqual(saidIn(sec).text, "Choose the file from this device.");
  assert.strictEqual(picker(sec).hidden, false);
  assert.strictEqual(picker(sec).clicks, 1);

  // -- a paired device: the picker at once, each file in turn ---------------------------------------
  reset();
  sec = await open("192.168.1.5");
  add(sec).click();
  assert.strictEqual(picker(sec).clicks, 1);                     // in the click itself, and hidden is fine for that
  assert.deepStrictEqual(posts("/v1/knowledge/pick"), []);
  reply["POST /v1/knowledge/upload"] = (b, url) => (url.includes("big.pdf") ? { status: 413, data: { error: "x" } } : url.includes("bad.md") ? { status: 400, data: { error: "That name can't be used." } } : null);
  const chosen = [file("one.docx"), file("keys.pem"), file("empty.md", 0), file("big.pdf"), file("bad.md"), file("two.txt")];
  const input = picker(sec);
  choose(input, chosen);
  assert.deepStrictEqual(input.files, []);                       // cleared, so the same file can be chosen again
  await settle();
  assert.deepStrictEqual(calls.filter((c) => c[1] === "/v1/knowledge/upload").map((c) => c[2].name), ["one.docx", "big.pdf", "bad.md", "two.txt"]);   // the others are said before anything is sent
  assert.deepStrictEqual(lineTexts(sec), ["one.docx is ready.", "keys.pem: PuffPAI can read .txt, .md, .docx and .pdf files.", "empty.md: That file is empty, so nothing was saved.",
    "big.pdf: " + L.UPLOAD_TOO_BIG, "bad.md: That name can't be used.", "two.txt is ready."]);
  assert.deepStrictEqual(posts("/v1/knowledge/pinned"), []);
  // no file chosen: nothing happens
  const quiet = tileOf(sec);
  choose(picker(sec), []);
  await settle();
  assert.strictEqual(tileOf(sec), quiet);

  // -- a whole folder -----------------------------------------------------------------------------
  // the bot's computer: the dialog, then the link
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { path: "C:\\Recipes" } });
  folder(sec).click();
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/pick"), [{ kind: "folder" }]);
  assert.deepStrictEqual(posts("/v1/folders/link"), [{ path: "C:\\Recipes" }]);
  assert.ok(tileOf(sec).textContent.includes("0 DOCUMENTS, 1 FOLDER"));
  assert.ok(tileOf(sec).find("data-documents", "linked").textContent.includes("C:\\Recipes"));
  assert.ok(tileOf(sec).all((n) => n.tag === "h3" && n.text === "Folders").length === 1);
  assert.ok(tileOf(sec).all((n) => n.tag === "button" && n.text === "Remove").length === 1);

  // closed: nothing
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { path: "" } });
  folder(sec).click();
  await settle();
  assert.deepStrictEqual(posts("/v1/folders/link"), []);
  assert.strictEqual(saidIn(sec).textContent, "");
  assert.strictEqual(tileOf(sec).find("data-folder-typed").open, false);

  // a bot with no dialog: the Core's words, and the path box open
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { path: "", typed: true, line: "Type it." } });
  folder(sec).click();
  await settle();
  assert.strictEqual(saidIn(sec).text, "Type it.");
  assert.strictEqual(tileOf(sec).find("data-folder-typed").open, true);

  // a paired device: today's line and a path box, and no dialog. A bad path says why; a good one links.
  reset();
  sec = await open("192.168.1.5");
  folder(sec).click();
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/pick"), []);
  assert.ok(saidIn(sec).text.startsWith("A folder is read where it is, so it can't come from this device."));
  const typed = tileOf(sec).find("data-folder-typed");
  assert.strictEqual(typed.hidden, false); assert.strictEqual(typed.open, true);
  assert.strictEqual(typed.kids[0].text, "Or type its path");
  const pathBox = tileOf(sec).find("aria-label", "A folder to add");
  pathBox.value = "Recipes";
  tileOf(sec).all((n) => n.tag === "button" && n.text === "Add this folder")[0].click();
  assert.ok(/whole path/.test(saidIn(sec).text));
  pathBox.value = '"D:\\Recipes"';
  tileOf(sec).all((n) => n.tag === "button" && n.text === "Add this folder")[0].click();
  await settle();
  assert.deepStrictEqual(posts("/v1/folders/link"), [{ path: "D:\\Recipes" }]);
  // no typed path for files: the one text box in the tile is the folder's
  assert.strictEqual(tileOf(sec).all((n) => n.tag === "input" && n.attrs.type === "text").length, 1);

  // -- the rows -----------------------------------------------------------------------------------
  reset();
  docs = [{ name: "Novel.docx", path: "C:\\b\\Novel.docx", line: "Ready.", ok: true, named: true, fiction: false },
    { name: "Plain.pdf", path: "C:\\b\\Plain.pdf", line: "Not there right now.", ok: false, named: false },
    { name: "Sent.md", path: "C:\\d\\uploads\\Sent.md", line: "Ready.", ok: true, named: false, copy: true }];
  sec = await open();
  assert.ok(tileOf(sec).textContent.includes("3 DOCUMENTS, 0 FOLDERS"));
  const rows = () => tileOf(sec).find("data-documents", "list").kids;
  assert.deepStrictEqual(rows().map((r) => r.attrs["data-document"]), docs.map((d) => d.path));
  const row = (i) => rows()[i];
  assert.strictEqual(row(0).find("data-named").checked, true);
  assert.ok(row(0).find("data-fiction"));                        // only when named
  assert.ok(!row(0).textContent.includes("keeps its name in mind") && !row(1).textContent.includes("keeps its name in mind"));
  assert.ok(tileOf(sec).find("data-named-note").text === "Name it in every chat: your bot keeps its name in mind and can read it start to finish.");
  assert.ok(!row(1).find("data-fiction") && !row(2).find("data-fiction"));
  assert.ok(row(1).textContent.includes("Name it in every chat"));
  assert.ok(row(1).all((n) => n.text === "Not there right now." && n.className.includes("warn-t")).length === 1);
  assert.ok(row(0).all((n) => n.text === "Ready." && !n.className.includes("warn-t")).length === 1);
  assert.ok(row(2).textContent.includes("A copy on your bot. To bring in changes, send it again."));
  assert.ok(!row(1).textContent.includes("A copy on your bot."));
  assert.ok(row(0).all((n) => n.tag === "b" && n.text === "Novel.docx").length === 1);

  // Name it in every chat: the route, and it goes back if the Core says no
  reply["POST /v1/knowledge/documents/named"] = () => ({ status: 200, data: { documents: [] } });
  const namedBox = row(1).find("data-named");
  namedBox.checked = true;
  await namedBox.on.change();
  assert.deepStrictEqual(posts("/v1/knowledge/documents/named"), [{ path: "C:\\b\\Plain.pdf", on: true }]);
  reply["POST /v1/knowledge/documents/named"] = () => ({ status: 404, data: { error: "That is not on your list." } });
  const back = row(1).find("data-named");
  back.checked = true;
  await back.on.change();
  assert.strictEqual(back.checked, false);
  assert.strictEqual(saidIn(sec).text, "That is not on your list.");

  // Remove: an original at once, and said at the top
  reply["POST /v1/knowledge/documents/remove"] = (b) => ({ status: 200, data: { said: L.fileNameOf(b.path) + " is off your list. The file itself stays where it is." } });
  row(1).find("data-remove-document").click();
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/documents/remove"), [{ path: "C:\\b\\Plain.pdf" }]);
  assert.deepStrictEqual(lineTexts(sec), ["Plain.pdf is off your list. The file itself stays where it is."]);
  // Remove on a copy asks on the same button
  const gone = row(2).find("data-remove-document");
  gone.click();
  await settle();
  assert.strictEqual(gone.text, "Delete this copy from your bot?");
  assert.strictEqual(posts("/v1/knowledge/documents/remove").length, 1);
  gone.click();
  await settle();
  assert.deepStrictEqual(posts("/v1/knowledge/documents/remove")[1], { path: "C:\\d\\uploads\\Sent.md" });

  // the list can't be read: said, never "Nothing here yet"
  reset();
  reply["GET /v1/knowledge/documents"] = () => ({ status: 500, data: { error: "It did not answer." } });
  sec = await open();
  assert.ok(tileOf(sec).textContent.includes("It did not answer.") && !tileOf(sec).textContent.includes("Nothing here yet"));

  // -- added but not readable (ok false): the Core's words, unchanged, as a warning --------------------
  const unread = "old.docx was added, but your bot can't read it. It is damaged.";
  assert.deepStrictEqual(await L.addInTurn([{ name: "old.docx" }], async () => ({ said: unread, ok: false }), () => {}), [{ text: unread, fail: true }]);
  reset();
  sec = await open();
  reply["POST /v1/knowledge/pick"] = () => ({ status: 200, data: { paths: ["C:\\a\\old.docx", "C:\\a\\new.docx"] } });
  reply["POST /v1/knowledge/documents"] = (b) => (b.path.endsWith("old.docx") ? { status: 200, data: { ok: false, said: unread } } : { status: 200, data: { ok: true, said: "new.docx is ready." } });
  add(sec).click();
  await settle();
  assert.deepStrictEqual(lineTexts(sec), [unread, "new.docx is ready."]);
  assert.deepStrictEqual(saidIn(sec).kids.map((k) => k.className), ["fail warn-t", ""]);
  reset();
  sec = await open("192.168.1.5");
  reply["POST /v1/knowledge/upload"] = () => ({ status: 200, data: { ok: false, said: unread } });
  choose(picker(sec), [file("old.docx")]);
  await settle();
  assert.deepStrictEqual(lineTexts(sec), [unread]);
  assert.strictEqual(saidIn(sec).kids[0].className, "fail warn-t");

  // -- the batch's state outlives any one tile --------------------------------------------------------
  // the page drawn again mid-batch, and the batch ends while that draw still waits on another tile
  reset();
  sec = await open("192.168.1.5");
  let upGo, filesGo;
  reply["POST /v1/knowledge/upload"] = () => new Promise((go) => { upGo = () => go({ status: 200, data: { said: "a.txt is ready." } }); });
  reply["GET /v1/knowledge/files"] = () => new Promise((go) => { filesGo = () => go({ status: 200, data: { files: [] } }); });
  choose(picker(sec), [file("a.txt")]);
  await settle();
  let sec2 = el("div");
  let drawn = defs.knowledge.draw(sec2);
  await settle();
  upGo();
  await settle();
  filesGo();
  await drawn;
  await settle();
  assert.deepStrictEqual(tileOf(sec2).querySelectorAll("button, input").filter((n) => n.disabled), []);
  assert.deepStrictEqual(lineTexts(sec2), ["a.txt is ready."]);
  const seen = calls.length;
  reply["POST /v1/knowledge/upload"] = null;
  add(sec2).click();
  assert.strictEqual(picker(sec2).clicks, 1);                    // Add documents works
  assert.strictEqual(calls.length, seen);

  // the same, with the batch's redraw still fetching when the draw goes in: its tile takes over when ready
  reset();
  sec = await open("192.168.1.5");
  reply["POST /v1/knowledge/upload"] = () => new Promise((go) => { upGo = () => go({ status: 200, data: { said: "b.txt is ready." } }); });
  reply["GET /v1/knowledge/files"] = () => new Promise((go) => { filesGo = () => go({ status: 200, data: { files: [] } }); });
  choose(picker(sec), [file("b.txt")]);
  await settle();
  sec2 = el("div");
  drawn = defs.knowledge.draw(sec2);
  await settle();
  let listGo;
  reply["GET /v1/knowledge/documents"] = () => new Promise((go) => { listGo = () => go({ status: 200, data: { documents: [{ name: "b.txt", path: "C:\\d\\uploads\\b.txt", ok: true, copy: true }] } }); });
  upGo();
  await settle();
  filesGo();
  await drawn;
  await settle();
  listGo();
  await settle();
  assert.deepStrictEqual(tileOf(sec2).querySelectorAll("button, input").filter((n) => n.disabled), []);
  assert.deepStrictEqual(lineTexts(sec2), ["b.txt is ready."]);
  assert.ok(tileOf(sec2).find("data-document", "C:\\d\\uploads\\b.txt"));

  // a draw whose list was asked for before the batch ended reads it again, so the new document is on it
  reset();
  sec = await open("192.168.1.5");
  reply["POST /v1/knowledge/upload"] = () => new Promise((go) => { upGo = () => go({ status: 200, data: { said: "c.txt is ready." } }); });
  choose(picker(sec), [file("c.txt")]);
  await settle();
  let oldList;
  reply["GET /v1/knowledge/documents"] = () => new Promise((go) => { oldList = () => go({ status: 200, data: { documents: [] } }); });
  sec2 = el("div");
  drawn = defs.knowledge.draw(sec2);
  await settle();
  reply["GET /v1/knowledge/documents"] = () => ({ status: 200, data: { documents: [{ name: "c.txt", path: "C:\\d\\uploads\\c.txt", ok: true, copy: true }] } });
  upGo();
  await settle();
  oldList();
  await drawn;
  await settle();
  assert.ok(tileOf(sec2).find("data-document", "C:\\d\\uploads\\c.txt"));
  assert.deepStrictEqual(lineTexts(sec2), ["c.txt is ready."]);
  assert.strictEqual(add(sec2).disabled, false);

  // a batch that ends while she is on another page: its lines are there when she comes back, once
  reset();
  sec = await open("192.168.1.5");
  reply["POST /v1/knowledge/upload"] = () => new Promise((go) => { upGo = () => go({ status: 200, data: { said: "d.txt is ready." } }); });
  choose(picker(sec), [file("d.txt")]);
  await settle();
  upGo();
  await settle();
  sec = await open("192.168.1.5");
  assert.deepStrictEqual(lineTexts(sec), ["d.txt is ready."]);
  sec = await open("192.168.1.5");
  assert.deepStrictEqual(lineTexts(sec), []);

  // -- what went away, and the words ------------------------------------------------------------------
  const page = fs.readFileSync(path.join(web, "pages", "knowledge.js"), "utf8");
  for (const old of ["function adder", "D.filePath", "Pick a file", "A file to pin", "Pinned documents"]) assert.ok(!page.includes(old), old);
  assert.ok(page.includes("Only your documents and your bot's library are searched."));
  assert.ok(!/style\s*:/.test(page));
  for (const f of ["pages/knowledge.js", "sendfile.js", "pages/developer.js", "logic.js"]) {
    const text = fs.readFileSync(path.join(web, f), "utf8");
    assert.ok(!text.includes("\u2014"), f);
    assert.ok(!/\bbeta\b/i.test(text), f);
  }
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
