// Lane I19a (D68): the build's keys in the Desktop's build view (packages/desktop/web/logic.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

// keys: only a plain true counts; anything else never asks for her PIN
assert.deepStrictEqual(L.buildKeys({ keys: { cloud: true, leased: false, unlock: true } }), { cloud: true, leased: false, unlock: true });
assert.deepStrictEqual(L.buildKeys({ keys: { unlock: "yes" } }), { cloud: false, leased: false, unlock: false });
assert.deepStrictEqual(L.buildKeys(null), { cloud: false, leased: false, unlock: false });
assert.deepStrictEqual(L.buildKeys({}), { cloud: false, leased: false, unlock: false });

// the build view carries it, for a recipe bot and an invoices bot alike
for (const goal of ["a home recipe bot", "a small-business invoices bot"]) {
  const b = { id: "b1", goal: goal, yes: { given: true }, keys: { cloud: true, leased: false, unlock: true },
              jobs: [{ id: "b1-1", name: "Job", status: "waiting_vault", run: { state: "waiting_vault" }, plan: {} }] };
  const v = L.buildView(b);
  assert.strictEqual(v.unlock, true);
  assert.strictEqual(v.active, true);
  assert.strictEqual(L.buildView(Object.assign({}, b, { keys: { cloud: true, leased: true, unlock: false } })).unlock, false);
}

// plain words, no em dash
for (const t of [L.UNLOCK_FOR_YES, L.UNLOCK_WAITING, L.UNLOCK_TYPE_PIN]) {
  assert.ok(typeof t === "string" && t.length > 10);
  assert.ok(t.indexOf("\u2014") < 0);
}
assert.ok(L.UNLOCK_FOR_YES.indexOf("PIN") >= 0 && L.UNLOCK_FOR_YES.indexOf("until this build ends") >= 0);
console.log("ok");
