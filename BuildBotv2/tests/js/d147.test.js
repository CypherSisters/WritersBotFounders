// D147: the Developer page's words for a program on another computer. Run by tests/test_d147_program_keys_lan.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

// On and listening: each address, with the two families. An Ollama-style program adds /api/chat itself.
const on = L.programWhere({ on: true, open: true, urls: ["http://192.168.1.5:8423/", "http://mybox.local:8423/"] });
assert.deepStrictEqual(on.rows, [
  { openai: "http://192.168.1.5:8423/openai/v1", ollama: "http://192.168.1.5:8423/ollama" },
  { openai: "http://mybox.local:8423/openai/v1", ollama: "http://mybox.local:8423/ollama" },
]);
assert.ok(/model name puffpai/.test(on.line));
assert.ok(/\/ollama\/api\/chat/.test(on.line));
assert.ok(/address on your home network/.test(on.line) && /may not work/.test(on.line));
// No slash at the end: the same address, never two slashes or a joined word.
assert.strictEqual(L.programWhere({ on: true, urls: ["http://192.168.1.5:8423"] }).rows[0].openai, "http://192.168.1.5:8423/openai/v1");

// Off: no address, and how to turn it on. An address sent with it is not shown.
const off = L.programWhere({ on: false, urls: ["http://192.168.1.5:8423/"] });
assert.deepStrictEqual(off.rows, []);
assert.ok(/closed to your home network/.test(off.line) && /Settings/.test(off.line));
assert.ok(!/8423/.test(off.line));

// Nothing to read, or not the shape: closed, the safe answer.
for (const bad of [null, undefined, "on", [], {}, { on: "true" }, { on: 1 }]) {
  assert.deepStrictEqual(L.programWhere(bad).rows, [], JSON.stringify(bad));
  assert.ok(/closed to your home network/.test(L.programWhere(bad).line));
}

// On, but the port is taken: the Core's own line, no address.
const busy = L.programWhere({ on: true, open: false, line: "Another program is using port 8423.", urls: ["http://192.168.1.5:8423/"] });
assert.deepStrictEqual(busy, { line: "Another program is using port 8423.", rows: [] });
assert.ok(/not listening/.test(L.programWhere({ on: true, open: false }).line));

// On, with no address on this computer.
for (const urls of [[], undefined, "x"]) {
  const none = L.programWhere({ on: true, open: true, urls });
  assert.deepStrictEqual(none.rows, []);
  assert.ok(/no home-network address/.test(none.line));
}

// The command makes a key on the computer running the bot, and carries none.
assert.ok(/^puffpai apikey new --label /.test(L.PROGRAM_KEY_COMMAND));
assert.ok(!/ppk_/.test(L.PROGRAM_KEY_COMMAND));

// The Settings row says a program with a key gets in too, and keeps its words for a device.
const row = L.networkView({ on: true, open: true, urls: ["http://192.168.1.5:8423/"], worth_knowing: "w" }, true);
assert.ok(/only a device you pair/.test(row.line) && /program with a key/.test(row.line));
assert.ok(/program keys/.test(row.note));
assert.ok(/program with a key/.test(L.networkView({ on: false }, true).line));

console.log("ok");
