// Lane I18g: the GitHub block's logic (packages/desktop/web/github.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const G = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "github.js"));

// the link state: linked, not linked, no vault, can't tell while locked
const v = (data, status) => G.keyView({ status: status || 200, data: data });
assert.deepStrictEqual([v({ linked: true, vault: "open", line: "L" }).state, v({ linked: true }).unlink, v({ linked: true }).form],
                       ["linked", true, false]);
assert.deepStrictEqual([v({ linked: false, vault: "open" }).state, v({ linked: false }).form, v({ linked: false }).unlink],
                       ["not_linked", true, false]);
assert.deepStrictEqual([v({ linked: null, vault: "not_set_up" }).state, v({ linked: null, vault: "not_set_up" }).setup,
                        v({ linked: null, vault: "not_set_up" }).form], ["no_vault", true, false]);
const locked = v({ linked: null, vault: "locked" });
assert.strictEqual(locked.state, "cant_tell");
assert.strictEqual(locked.title, "Can't tell while your vault is locked");
assert.strictEqual(locked.form, true);
assert.strictEqual(v(null, 500).state, "error");
assert.strictEqual(G.keyView(null).state, "error");

// a key's shape, as the Core checks it; the text is never in the line
const KEY = "github_pat_" + "0Fake".repeat(8);
assert.strictEqual(G.keyShape(KEY).ok, true);
assert.strictEqual(G.keyShape("  " + KEY + "\n").ok, true);
for (const bad of ["", "short", KEY.slice(0, 12) + " " + KEY.slice(12), KEY.slice(0, 12) + "\n" + KEY.slice(12), KEY + "é", "A".repeat(256)]) {
  const got = G.keyShape(bad);
  assert.strictEqual(got.ok, false, bad);
  if (bad) assert.ok(got.line.indexOf(bad.trim()) < 0);
}

// a pasted address: one line naming github.com; the Core reads its form
assert.deepStrictEqual(G.addressCheck(" https://github.com/example/news-a "), { ok: true, address: "https://github.com/example/news-a", line: "" });
assert.strictEqual(G.addressCheck("git@github.com:example/news-a.git").ok, true);
assert.strictEqual(G.addressCheck("").line, "");
assert.strictEqual(G.addressCheck("a recipe box").ok, false);
assert.strictEqual(G.addressCheck("https://github.com/a/b\nx").ok, false);
assert.strictEqual(G.addressCheck("https://github.com/" + "a".repeat(500)).ok, false);

// "Use it for": only the last build's jobs that are held or found nothing
const st = { builds: [
  { id: "20261027-100000-aaaaaa", goal: "old", created_at: "2026-10-27T10:00:00", jobs: [{ id: "20261027-100000-aaaaaa-0", name: "Old", status: "held" }] },
  { id: "20261028-100000-bbbbbb", goal: "news", created_at: "2026-10-28T10:00:00", jobs: [
    { id: "20261028-100000-bbbbbb-0", name: "Headlines", status: "working" },
    { id: "20261028-100000-bbbbbb-1", name: "Weather", status: "nothing_found" },
    { id: "20261028-100000-bbbbbb-2", name: "Map", status: "held" },
    { id: "20261028-100000-bbbbbb-3", name: "Sports", status: "building: x" }] }] };
const held = G.heldJobs(st);
assert.deepStrictEqual(held.map((j) => [j.id, j.name, j.build]), [["20261028-100000-bbbbbb-1", "Weather", "20261028-100000-bbbbbb"],
  ["20261028-100000-bbbbbb-2", "Map", "20261028-100000-bbbbbb"]]);
assert.strictEqual(G.useLabel(held[0]), "Use it for: Weather");
assert.deepStrictEqual(G.heldJobs({ builds: [] }), []);
assert.deepStrictEqual(G.heldJobs(null), []);

// the token steps and page: read-only, on github.com
assert.ok(G.TOKEN_PAGE.indexOf("https://github.com/") === 0);
assert.ok(G.TOKEN_STEPS.some((s) => s.indexOf("read-only") >= 0));
assert.ok(G.SENT_LINE.indexOf("api.github.com only") >= 0);

console.log("ok");
