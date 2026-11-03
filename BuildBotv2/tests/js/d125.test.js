// D125, D126: the words for her documents (packages/desktop/web/devzones.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const D = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "devzones.js"));

// a path as she pastes it: File Explorer's Copy as path adds quotes. Only a folder is typed now; a file comes from Add documents.
assert.strictEqual(D.filePath, undefined);
assert.deepStrictEqual(D.folderPath('"C:\\Users\\me\\My Novels"'), { ok: true, path: "C:\\Users\\me\\My Novels", line: "" });
assert.deepStrictEqual(D.folderPath("  \\\\server\\share\\a "), { ok: true, path: "\\\\server\\share\\a", line: "" });
assert.strictEqual(D.folderPath("").ok, false);
assert.strictEqual(D.folderPath("").line, "");
assert.ok(/whole path/.test(D.folderPath("My Novels").line));
assert.ok(/One folder/.test(D.folderPath("a\nb").line));
assert.ok(/One folder/.test(D.folderPath("C:\\" + "x".repeat(1000)).line));

// a folder's row says what it read and what it couldn't, in the Core's words; with none, as before
const rows = (linked) => D.foldersView({ linked: linked }).linked.map((x) => x.line);
assert.deepStrictEqual(rows([{ path: "C:\\a", there: true, line: "1 file read. 1 file BuildBot can't read yet: old.doc." }]),
  ["1 file read. 1 file BuildBot can't read yet: old.doc."]);
assert.deepStrictEqual(rows([{ path: "C:\\a", there: true }]), ["There"]);
assert.deepStrictEqual(rows([{ path: "C:\\a", there: false, line: "stale" }]), ["Not there right now, so it is not read"]);

// D127: which apps take a file, and a file's bytes for the Core
const apps = [{ id: "s", types: [".docx", "text/*"] }, { id: "c", types: [".docx", ".xlsx"] }];
assert.deepStrictEqual(D.sendFits(apps, "A.DOCX").map((t) => t.id), ["s", "c"]);
assert.deepStrictEqual(D.sendFits(apps, "notes.md").map((t) => t.id), ["s"]);
assert.deepStrictEqual(D.sendFits(apps, "a.pdf"), []);
assert.deepStrictEqual(D.sendFits(apps, "no-ending"), []);
assert.deepStrictEqual(D.sendFits(null, "a.docx"), []);
assert.strictEqual(D.dataUrlBase64("data:text/plain;base64,eA=="), "eA==");
assert.deepStrictEqual(D.foldersView({ linked: [{ path: "C:\\a", there: true, files: [{ name: "x", path: "C:\\a\\x" }] }] }).linked[0].files, [{ name: "x", path: "C:\\a\\x" }]);

// D140: a fix the bot shows: only text, and where it is
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
const turn = new L.ChatTurn();
turn.add({ type: "passage", document: "N.docx", part: 2, of: 9, before: "a ", passage: "b", after: " c", suggestion: "d", why: "e" });
assert.deepStrictEqual(turn.passages, [{ where: "N.docx, part 2 of 9", before: "a ", passage: "b", after: " c", suggestion: "d", why: "e" }]);
assert.strictEqual(L.passageView({ document: "N", passage: 3 }).passage, "");

console.log("ok");
