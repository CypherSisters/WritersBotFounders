// D96: the Desktop's one store panel, from the built-in packages and the store's index. Run by tests/test_d96_store_read.py.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");

const SVG = "data:image/svg+xml;base64,AAAA";
const row = (id, more) => Object.assign({ id, kind: "app", name: id, description: "Does a thing. And more.", version: "1.0.0",
  author: "A Maker", tier: "verified", official: true, source: "https://store.invalid/index.json", used: true, yanked: false,
  installed: false, builtin_look: false, icon: "https://raw.githubusercontent.com/o/r/c/icon.svg" }, more || {});

const built = [{ id: "freshtable", name: "FreshTable", kind: "app", description: "Recipes.", version: "2.0.0", author: "Cypher Sisters", icon: SVG },
  { id: "example-page", name: "My notes", kind: "page", description: "An example." }, { id: "desktop", name: "Desktop", kind: "ui", description: "x" },
  { id: "look-paper", name: "Paper", kind: "look", description: "Paper." }];
const shelf = L.storeShelf(built, { sources: [], packages: [
  row("freshtable", { name: "A second FreshTable" }), row("gardenlog"), row("look-new", { kind: "look" }), row("avatar-x", { kind: "avatar" }),
  row("a-tool", { kind: "tool" }), row("a-skill", { kind: "skill" }), row("a-agent", { kind: "agent" }), row("a-bundle", { kind: "bundle" }),
  row("later-copy", { used: false }), row("on-my-bot", { installed: true }), row("look-clean", { kind: "look", name: "Clean", builtin_look: true }),
  row("hers", { official: false, tier: "community", source: "C:/my store" }), row("claims", { official: false, tier: "verified", source: "C:/my store" }),
  row("pulled", { yanked: true, yanked_why: "It broke." }), row("bare", { tier: "community" }) ] });
const titles = Array.from(shelf.sections, (s) => s.title);
assert.deepStrictEqual(titles, ["Apps", "Looks", "Avatars", "Tools", "Skills", "Other parts"]);
const items = (t) => Array.from(shelf.sections.find((s) => s.title === t).items, (p) => p.id);
assert.deepStrictEqual(items("Apps"), ["freshtable", "gardenlog", "hers", "claims", "pulled", "bare"]);
assert.deepStrictEqual(items("Looks"), ["look-paper", "look-new"]);
assert.deepStrictEqual(items("Other parts"), ["a-agent", "a-bundle"]);
const all = Array.from(shelf.rows, (p) => p.id);
assert.strictEqual(all.indexOf("later-copy"), -1);          // a later source's duplicate
assert.strictEqual(all.indexOf("on-my-bot"), -1);
assert.strictEqual(all.indexOf("look-clean"), -1);          // never two Cleans
assert.strictEqual(all.indexOf("desktop"), -1);             // the Desktop and the example are not for her to pick
assert.strictEqual(all.indexOf("example-page"), -1);
assert.strictEqual(new Set(all).size, all.length);          // one row per id
const by = (id) => shelf.rows.find((p) => p.id === id);
// the built-in row wins, keeps its data: icon, adds by its id and needs no download
assert.strictEqual(by("freshtable").name, "FreshTable");
assert.strictEqual(by("freshtable").icon, SVG);
assert.strictEqual(by("freshtable").add, undefined);
assert.strictEqual(by("freshtable").store, undefined);
// a store row never carries a remote picture, and adds as store:<id>
shelf.rows.filter((p) => p.store).forEach((p) => {
  assert.strictEqual(p.icon, null);
  assert.strictEqual(p.add, "store:" + p.id);
  assert.ok(!JSON.stringify(p).includes("raw.githubusercontent"), p.id);
});
assert.strictEqual(by("gardenlog").glyph, "widgets");
assert.strictEqual(by("a-tool").glyph, "build");
assert.strictEqual(by("a-agent").glyph, "extension");       // a kind nobody listed still has a glyph
assert.strictEqual(by("gardenlog").tierLine, "Verified: PuffPAI read it at that exact commit.");
assert.strictEqual(by("gardenlog").from, "");
assert.strictEqual(by("gardenlog").by, "By A Maker, version 1.0.0");
assert.strictEqual(by("gardenlog").line, "Does a thing.");
assert.strictEqual(by("bare").tierLine, "Community: PuffPAI has not read it.");
assert.strictEqual(by("hers").from, "From C:/my store.");
// only the official store's Verified reads as Verified, whatever a row says
assert.strictEqual(by("claims").tierLine, "Community: PuffPAI has not read it.");
assert.strictEqual(by("pulled").pulled, "Not offered. The store pulled it: It broke.");
assert.strictEqual(shelf.count, shelf.rows.length - 1);     // a pulled row is not counted as ready

// an id that is also a name on every JS object is only an id
const oddIds = L.storeShelf([], { sources: [], packages: [row("constructor"), row("tostring"), row("valueof")] });
assert.deepStrictEqual(Array.from(oddIds.rows, (p) => p.id), ["constructor", "tostring", "valueof"]);
assert.deepStrictEqual(Array.from(L.storeShelf([{ id: "constructor", kind: "app", name: "Built in" }], { packages: [row("constructor")] }).rows, (p) => p.name), ["Built in"]);

// a source that is out of date or unreachable says so; the built-in rows still list; 404 today reads fine
const down = L.storeShelf(built, { packages: [], sources: [
  { source: "official", official: true, ok: false, stale: true, line: "Could not reach the official PuffPAI store: it answered 404." },
  { source: "C:/a", ok: true, stale: true, line: "The copy may be out of date." }, { source: "C:/b", ok: true, stale: false, line: "3 packages." }] });
assert.deepStrictEqual(Array.from(down.lines), ["Could not reach the official PuffPAI store: it answered 404.", "The copy may be out of date."]);
assert.deepStrictEqual(Array.from(down.rows, (p) => p.id), ["freshtable", "look-paper"]);
assert.deepStrictEqual(Array.from(L.storeShelf(built, null).rows, (p) => p.id), ["freshtable", "look-paper"]);
assert.deepStrictEqual(Array.from(L.storeShelf(null, null).rows), []);
assert.deepStrictEqual(Array.from(L.storeShelf(built, { packages: [null, 4, { id: 1 }, { id: "x" }], sources: [null] }).rows, (p) => p.id), ["freshtable", "look-paper"]);

// Home: built-ins plus the saved copy's official apps, never a community source's, a pulled row or another kind
const setupDone = { wizard_complete: true };
const saved = { sources: [], packages: [row("gardenlog"), row("hers", { official: false, tier: "community" }), row("pulled", { yanked: true }), row("a-tool", { kind: "tool" })] };
assert.deepStrictEqual(Array.from(L.storePrompt(setupDone, {}, built, saved).apps, (p) => p.id), ["freshtable", "gardenlog"]);
assert.strictEqual(L.storePrompt(setupDone, {}, built, saved).apps[1].add, "store:gardenlog");
assert.strictEqual(L.storePrompt(setupDone, {}, [], saved).show, true);       // only the saved copy has one
assert.strictEqual(L.storePrompt(setupDone, {}, built, null).apps.length, 1); // no saved copy: built-ins only
assert.strictEqual(L.storePrompt(setupDone, {}, [], null).show, false);

// a plan's store:<id> package needs nothing of the id: lines and the card come from the Core's own record
const progress = L.storeProgress({ store_adds: { state: "adding", line: "", apps: [{ package: "store:gardenlog", name: "Garden Log", state: "asking", card: "pm_1", line: "Garden Log: its own Yes card is waiting." }] } });
assert.strictEqual(progress.asking, "pm_1");
assert.deepStrictEqual(Array.from(progress.lines), ["Garden Log: its own Yes card is waiting."]);

// the panel's own code never draws a picture from a row of the index, and asks for the network only when she opens it
const apps = fs.readFileSync(path.join(web, "pages", "apps.js"), "utf8");
const home = fs.readFileSync(path.join(web, "pages", "home.js"), "utf8");
assert.ok(home.includes('"/v1/store?saved=1"'));
assert.ok(!/"\/v1\/store"/.test(home));
assert.strictEqual((apps.match(/"\/v1\/store" \+/g) || []).length, 1);
assert.ok(!/P\.on\(/.test(apps));                           // no event redraws the panel behind her back
console.log("ok");
