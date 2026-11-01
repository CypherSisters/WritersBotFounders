// The "Web search with your own key" block's logic (packages/desktop/web/searchkeys.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const S = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "searchkeys.js"));

// two rows, in plain words: Brave's cost and Marginalia's email are said
assert.deepStrictEqual(S.ROWS.map((r) => r.id), ["brave", "marginalia"]);
assert.ok(/no free plan/.test(S.ROWS[0].says) && /charges you for each search/.test(S.ROWS[0].says));
assert.ok(/by email/.test(S.ROWS[1].says) && /shares your address/.test(S.ROWS[1].says) && /your choice/.test(S.ROWS[1].says));

// off unless she links a key
assert.strictEqual(S.keyView({ status: 200, data: { linked: false, line: "Not linked." } }).state, "not_linked");
assert.strictEqual(S.keyView({ status: 200, data: { linked: true, yes: true, line: "Linked." } }).state, "linked");
const waiting = S.keyView({ status: 200, data: { linked: true, yes: false, line: "x" } });
assert.ok(waiting.state === "waiting" && waiting.form && waiting.unlink);
assert.strictEqual(S.keyView({ status: 200, data: { linked: null, vault: "not_set_up" } }).state, "no_vault");
assert.strictEqual(S.keyView({ status: 200, data: { linked: null, vault: "locked" } }).state, "cant_tell");
assert.strictEqual(S.keyView({ status: 500 }).state, "error");

// the shape, the same as the Core's; the text is never in the line
assert.ok(S.keyShape("BSAabc123xyz", "Brave Search").ok);
assert.ok(S.keyShape("public", "Marginalia Search").ok);
const bad = S.keyShape("not a key!", "Brave Search");
assert.ok(!bad.ok && bad.line.indexOf("not a key") < 0);
assert.ok(!S.keyShape("", "Brave Search").ok && !S.keyShape("abc", "Brave Search").ok);

console.log("ok");
