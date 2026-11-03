// D160: a library fetch that came up short says so, and "Check again" is there for it (packages/desktop/web/logic.js). Prints "ok".
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const missing = "Still missing: 5 Wikipedia articles. Check again reads what is missing.";

// Every job works, only the fetch came up short: Check again is offered.
const jobs = [{ id: "b-0", status: "working" }];
const v = L.buildView({ id: "b", yes: { given: true }, jobs: jobs, knowledge_missing: missing });
assert.strictEqual(v.canAgain, true);
assert.strictEqual(v.unfinished, 0);
assert.strictEqual(v.missing, missing);
assert.strictEqual(L.buildView({ id: "b", yes: { given: true }, jobs: jobs }).canAgain, false);
assert.strictEqual(L.buildView({ id: "b", jobs: jobs, knowledge_missing: missing }).canAgain, false);   // no Yes yet
assert.strictEqual(L.buildView({ id: "b", yes: { given: true }, again: { state: "asking", consent_id: "c1" }, jobs: jobs,
                                 knowledge_missing: missing }).againAsking, "c1");

// The library line for a short fetch: never "downloaded", and it goes where Check again is.
const both = ["Wikipedia", "Project Gutenberg"];
const s = L.libraryLine({ state: "short", done: 5, of: 10, up_to: false, sources: both, now: "stale", line: "Read 5. " + missing },
                        "a wedding planner bot", "b 1");
assert.strictEqual(s.line, "Library: stopped short, 5 of 10 from Wikipedia and Project Gutenberg");
assert.strictEqual(s.state, "short");
assert.strictEqual(s.fetching, false);
assert.strictEqual(s.now, "");
assert.ok(s.end.includes(missing));
assert.strictEqual(s.href, "#building/b%201");
assert.strictEqual(L.libraryLine({ state: "short", done: 0, of: 50, up_to: true, sources: ["Wikipedia"] }).line,
                   "Library: stopped short, 0 of up to 50 from Wikipedia");
assert.strictEqual(L.libraryNow({ builds: [{ id: "x", library: { state: "short", done: 1, of: 2, sources: both } }] }).id, "x");

console.log("ok");
