// Lane I18f: the Developer page's words and choices (packages/desktop/web/devzones.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const D = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "devzones.js"));

// the zone cards: v1's order, names and lines; never "open" while the vault is locked
const cards = D.zoneCards({ vault: { state: "locked" }, model: { settings: { model: "qwen3.5:9b" }, state: { state: "ready" } },
                            bridge: { state: "closed", setting: "open", search: true } });
assert.deepStrictEqual(cards.map((c) => [c.zone, c.name, c.href]), [["Zone 1", "Vault", "#developer/vault"],
  ["Zone 2", "Brain", "#developer/brain"], ["Zone 3", "Bridge", "#developer/bridge"]]);
assert.deepStrictEqual(cards.map((c) => c.status), ["Made, locked", "qwen3.5:9b, answering", "Closed, Toolsmith may search while it builds"]);
assert.strictEqual(D.vaultStatus({ state: "open" }), "Made, open now");
assert.strictEqual(D.vaultStatus({ state: "not_set_up" }), "Not made yet");
assert.strictEqual(D.vaultStatus(null), "Could not be read");
assert.strictEqual(D.brainStatus({ settings: { model: "" } }), "No model chosen yet");
assert.strictEqual(D.brainStatus({ settings: { model: "m" }, state: { state: "error" } }), "m, not answering right now");
assert.strictEqual(D.bridgeStatus({ state: "open", setting: "open" }), "Open: something may go out now");
assert.strictEqual(D.bridgeStatus({ state: "closed", setting: "closed", search: false }), "Closed");

// what can be done to the vault from here
assert.deepStrictEqual(D.vaultActions({ state: "open" }), { lock: true, unlock: false, setup: false, wait: 0 });
assert.deepStrictEqual(D.vaultActions({ state: "locked" }), { lock: false, unlock: true, setup: false, wait: 0 });
assert.strictEqual(D.vaultActions({ state: "throttled", retry_after: 9 }).wait, 9);
assert.strictEqual(D.vaultPlace({ state: "open", store: { ok: true, location: "C:\\v" } }).where, "C:\\v");
assert.strictEqual(D.vaultPlace({ state: "missing", store: { ok: false, reason: "Its drive is not plugged in." } }).line, "Its drive is not plugged in.");

// the folders: linked ones there or not, and what Setup saved but the bot does not use yet
const f = D.foldersView({ data: "C:\\d", data_there: true, default: false, linked: [{ path: "C:\\a", there: true }, { path: "D:\\b", there: false }],
                          from_setup: { data: "C:\\Users\\x\\PuffPAI Data", linked: ["C:\\r"] }, line: "L" });
assert.strictEqual(f.dataLine, "A folder you chose");
assert.deepStrictEqual(f.linked.map((x) => x.line), ["There", "Not there right now, so it is not read"]);
assert.ok(/a data folder \(C:\\Users\\x\\PuffPAI Data\) and 1 linked folder, but this bot does not use them yet/.test(f.setup.line), f.setup.line);
assert.strictEqual(D.foldersView({ linked: [] }).none, "No folders are linked yet.");
assert.strictEqual(D.foldersView({}).setup, null);
// where books and downloads are kept (D103): at rest, moving, and stopped
const at = D.placeView({ store: "E:\\b\\knowledge", free: "1.0 TB free on E:", default: false, line: "", move: null });
assert.strictEqual(at.where, "E:\\b\\knowledge"); assert.strictEqual(at.moving, false); assert.strictEqual(at.isDefault, false);
const go = D.placeView({ move: { phase: "copying", running: true, percent: 40, done_files: 2, total_files: 5, now: "Moving a.zim" } });
assert.strictEqual(go.running, true); assert.strictEqual(go.percent, 40); assert.strictEqual(go.progress, "2 of 5 files, Moving a.zim");
const stop = D.placeView({ move: { phase: "stopped", running: false, can_resume: true, can_cancel: true, line: "E: is not there." } });
assert.strictEqual(stop.canResume, true); assert.strictEqual(stop.canCancel, true); assert.strictEqual(stop.moveLine, "E: is not there.");
assert.strictEqual(D.placeView({ move: { phase: "done", line: "Moved." } }).moving, false);
assert.deepStrictEqual(D.folderPath('"C:\\Users\\me\\Recipes"'), { ok: true, path: "C:\\Users\\me\\Recipes", line: "" });
assert.strictEqual(D.folderPath("%USERPROFILE%\\Documents").ok, true);
assert.strictEqual(D.folderPath("Recipes").ok, false);
assert.strictEqual(D.folderPath("C:\\a\nC:\\b").ok, false);
assert.strictEqual(D.folderPath("").ok, false);

// Toolsmith: names only, and which cap change needs her Yes again
const now = D.toolsmithNow({ where: "cloud", provider: "anthropic", company: "Anthropic", model: "m1", cost_cap_usd: 5 },
                           [{ provider: "anthropic", kept: false }]);
assert.strictEqual(now.line, "Anthropic, m1");
assert.ok(/No key is kept yet/.test(now.key));
assert.ok(/Open your vault/.test(D.toolsmithNow({ where: "cloud", provider: "a" }, [{ provider: "a", kept: null }]).key));
assert.strictEqual(D.toolsmithNow({ where: "local" }, []).line, "This computer's own model");
assert.strictEqual(D.capChange(5, "2"), "lower");
assert.strictEqual(D.capChange(5, 5), "same");
assert.strictEqual(D.capChange(5, "7.5"), "higher");
assert.strictEqual(D.capChange(null, 1), "higher");          // local now: any cloud cap is a new Yes
assert.strictEqual(D.capChange(5, ""), "bad");
assert.strictEqual(D.capChange(5, "-1"), "bad");

// the hosts Toolsmith may reach, read only
const hosts = D.hostLines({ search: { hosts: ["api.github.com"], on: false, switch: true }, cloud: { on: false }, knowledge: { hosts: ["en.wikipedia.org"] } });
assert.deepStrictEqual(hosts.map((h) => h.state), ["On, opens when the Bridge is open", "Off: it uses this computer", "Only for that build"]);

// Use CLI: only commands this build has, never one that carries a PIN or a key
["overview", "vault", "brain", "bridge"].forEach((p) => {
  const cmds = D.cliCommands(p);
  assert.ok(cmds.length > 0, p);
  cmds.forEach((c) => { assert.ok(/^puffpai (check|add|pair|list|logs|notices|bridge|knowledge) ?/.test(c.command), c.command); assert.ok(!/pin|key|password/i.test(c.command), c.command); });
});
assert.deepStrictEqual(D.cliCommands("nothing"), []);

// This computer, from the census
assert.strictEqual(D.computerLine({ memory: { total_gb: 16 }, cpu: { model: "CPU", logical_cores: 8 }, accelerator: { kind: "none", name: "x" } }),
  "16 GB of memory, CPU, 8 cores");
assert.strictEqual(D.computerLine(null), "Could not be read");

console.log("ok");
