// Lane I19b: the picture model's words and the library line (packages/desktop/web/logic.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const BAD = new RegExp("\\u2014|doors|beta|warts", "i");

// -- the library line: fetching, done, waiting; for any goal ---------------------------------------------------
const both = ["Wikipedia", "Project Gutenberg"];
const f = L.libraryLine({ state: "fetching", done: 12, of: 50, up_to: false, sources: both, now: "Reading the book \"Bird Song\"" },
                        "a birdwatching bot", "b1");
assert.strictEqual(f.line, "Library: fetching 12 of 50 from Wikipedia and Project Gutenberg");
assert.strictEqual(f.note, "BuildBot is downloading these into your library on this computer. You are not on these websites.");
assert.strictEqual(f.now, "Reading the book \"Bird Song\"");
assert.strictEqual(f.href, "#knowledge");
assert.strictEqual(f.fetching, true);
assert.strictEqual(L.libraryLine({ state: "fetching", done: 0, of: 30, up_to: true, sources: ["Wikipedia"] }).line,
                   "Library: fetching 0 of up to 30 from Wikipedia");
const d = L.libraryLine({ state: "done", done: 7, of: 9, sources: both, now: "stale", line: "Read 7 Wikipedia articles into your bot's knowledge." });
assert.strictEqual(d.line, "Library: downloaded 7 of 9 from Wikipedia and Project Gutenberg");
assert.strictEqual(d.note, L.LIBRARY_DONE);
assert.strictEqual(d.now, "");                         // a done fetch has no "now"
assert.ok(/^Read 7/.test(d.end));
const w = L.libraryLine({ state: "waiting", done: 0, of: 40, up_to: true, sources: both });
assert.strictEqual(w.line, "Library: up to 40 from Wikipedia and Project Gutenberg, after your Yes");
assert.ok(/You do not visit these websites/.test(w.note));
assert.strictEqual(L.libraryLine(null), null);
assert.strictEqual(L.andList(["A", "B", "C"]), "A, B and C");

// Home and Knowledge: a build fetching now first, else the newest that fetched; nothing when none read yet
const status = { builds: [
  { id: "r", goal: "a home recipe bot with food photos", created_at: "2026-10-28T10:00:00", library: { state: "done", done: 4, of: 4, sources: ["Wikipedia"] } },
  { id: "i", goal: "a small business invoices bot", created_at: "2026-10-28T12:00:00", library: null },
  { id: "b", goal: "a birdwatching bot", created_at: "2026-10-28T09:00:00", library: { state: "fetching", done: 2, of: 20, sources: both } },
] };
assert.strictEqual(L.libraryNow(status).id, "b");
status.builds[2].library.state = "done";
assert.strictEqual(L.libraryNow(status).id, "r");     // newest done
assert.strictEqual(L.libraryNow({ builds: [{ id: "x", library: { state: "waiting" } }, { id: "y", library: null }] }), null);
assert.strictEqual(L.libraryNow(null), null);

// -- the picture model: it reads pictures, never makes one; says plainly when nothing used it ------------------
const build = { id: "p1", model_needs: ["vision"], use_fixed: { models: { vision: "gemma3:4b" } },
                store_adds: { apps: [{ kind: "model", role: "vision", model: "gemma3:4b", name: "Gemma 3 4B", state: "added" }] } };
const none = L.pictureModel(build, { vision: { model: "gemma3:4b", own: true, count: 0, last_at: "", last_by: "" } });
assert.strictEqual(none.head, "Picture model: Gemma 3 4B (gemma3:4b), on this computer.");
assert.strictEqual(none.what, "It reads pictures and says what is in them. It does not make pictures.");
assert.strictEqual(none.use, L.PICTURE_NONE);
assert.ok(/^Nothing has sent it a picture yet/.test(none.use));
const read = L.pictureModel(build, { vision: { model: "gemma3:4b", own: true, count: 3, last_at: "2026-10-28T23:01:00+00:00", last_by: "freshtable" } });
assert.strictEqual(read.use, "It has read 3 pictures so far, last for freshtable on 2026-10-28. Described by Gemma 3 4B on this computer.");
assert.strictEqual(L.pictureModel(build, { vision: { model: "gemma3:4b", count: 1 } }).use,
                   "It has read 1 picture so far. Described by Gemma 3 4B on this computer.");
assert.ok(/not the picture model now: qwen2.5vl:7b is/.test(L.pictureModel(build, { vision: { model: "qwen2.5vl:7b", count: 9 } }).now));
assert.strictEqual(L.pictureModel(build, null).use, "");     // the count could not be read: no claim either way
// a goal that needed a picture model, with none added: pictures go to the chat model
const need = L.pictureModel({ model_needs: ["vision"] }, { vision: { model: "qwen3.5:9b", own: false, count: 0 } });
assert.ok(/None was added for it, so a picture would go to qwen3.5:9b, the chat model\./.test(need.head), need.head);
// only a role tick (no store record): the model's own name
assert.strictEqual(L.pictureModel({ use_fixed: { models: { vision: "qwen2.5vl:7b" } } }, null).head,
                   "Picture model: qwen2.5vl:7b, on this computer.");
// an invoices bot that needs no picture model shows nothing
assert.strictEqual(L.pictureModel({ model_needs: ["code"], use_fixed: { models: { code: "qwen2.5-coder:7b" } } }, null), null);
assert.strictEqual(L.describedBy("Gemma 3 4B"), "Described by Gemma 3 4B on this computer.");

// -- the source link: its host, labeled; only https --------------------------------------------------------------
const k = L.knowledgeGroups({ items: [{ source: "gutenberg", title: "Birds of the Garden", url: "https://www.gutenberg.org/ebooks/7", size: 10 }] });
assert.strictEqual(k.groups[0].items[0].host, "www.gutenberg.org");
assert.strictEqual(L.SOURCE_LINK_NOTE, "Opens the source website in a new tab of your browser.");

[f, d, w, none, read, need].forEach((x) => Object.values(x).forEach((v) => assert.ok(!BAD.test(String(v)), String(v))));
console.log("ok");
