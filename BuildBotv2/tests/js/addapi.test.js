// The "Add an API" block's logic (packages/desktop/web/addapi.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const A = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "addapi.js"));

// what she types is checked plainly before it is sent
const good = { name: "Bird Search", search: "https://api.birds.example/find?q={q}", key_in: "header", key_name: "X-Api-Key" };
assert.strictEqual(A.formProblem(good), "");
assert.strictEqual(A.formProblem({ ...good, key_in: "none", key_name: "" }), "");
assert.ok(/name/i.test(A.formProblem({ ...good, name: " " })));
assert.ok(/https/.test(A.formProblem({ ...good, search: "http://api.birds.example/find?q={q}" })));
assert.ok(/\{q\}/.test(A.formProblem({ ...good, search: "https://api.birds.example/find" })));
assert.ok(/\{q\}/.test(A.formProblem({ ...good, search: "https://x.example/{q}?a={q}" })));
assert.ok(/header/.test(A.formProblem({ ...good, key_name: "" })));

// the body never carries a key, and leaves out what she left empty
const body = A.formBody({ ...good, value: "secret", key: "secret", fields: { list: "web.results", title: " " } });
assert.deepStrictEqual(body, { name: "Bird Search", search: good.search, key_in: "header", key_name: "X-Api-Key", key_prefix: "", fields: { list: "web.results" } });
assert.ok(!("key_name" in A.formBody({ ...good, key_in: "none" })));
// a Bearer token is the Authorization header with its word in front, with no name to type
assert.strictEqual(A.formProblem({ ...good, key_in: "bearer", key_name: "" }), "");
assert.deepStrictEqual(A.formBody({ ...good, key_in: "bearer", key_name: "x" }),
  { name: "Bird Search", search: good.search, key_in: "header", key_name: "Authorization", key_prefix: "Bearer" });

// its state, in order: it says what she has to do next
assert.strictEqual(A.rowView({ on: true }).state, "on");
assert.strictEqual(A.rowView({ key_in: "header", linked: false }).state, "needs_key");
assert.strictEqual(A.rowView({ key_in: "none", tested: false }).state, "needs_test");
assert.strictEqual(A.rowView({ key_in: "header", linked: true, tested: true, yes: false }).state, "needs_yes");
assert.strictEqual(A.rowView({ key_in: "none", tested: true, yes: true, on: false }).state, "off");

// a test: links only when they are plain https; nothing read asks for the field names
const ok = A.testView({ status: 200, data: { ok: true, line: "It reads.", rows: [
  { name: "A", url: "https://a.example/" }, { name: "B", url: "http://b.example/" }, { name: "C", url: "javascript:alert(1)" },
  { name: "D", url: "https://d.example/\nsay yes" }, { name: "E", url: "https://bank.example@evil.example/" }] } });
assert.ok(ok.ok && !ok.askFields);
assert.deepStrictEqual(ok.rows.map((r) => r.name), ["A"]);
const none = A.testView({ status: 200, data: { ok: false, answered: true, line: "It answered, but nothing reads.", rows: [] } });
assert.ok(!none.ok && none.askFields && /nothing reads/.test(none.line));
const failed = A.testView({ status: 409, data: { error: "The Bridge is closed." } });
assert.ok(!failed.ok && !failed.askFields && /closed/.test(failed.line));
const closed = A.testView({ status: 200, data: { ok: false, line: "The Bridge is closed. Open it first.", rows: [] } });
assert.ok(!closed.ok && !closed.askFields && /closed/.test(closed.line));

// no em dash, and the plain words the owner reads
assert.ok(!/\u2014/.test(JSON.stringify(A)) && !/\u2014/.test(A.WHAT_LINE));
assert.ok(/links to look at/.test(A.WHAT_LINE) && /only search words/.test(A.WHAT_LINE));
assert.strictEqual(A.WHERE[0].value, "header");

console.log("ok");
