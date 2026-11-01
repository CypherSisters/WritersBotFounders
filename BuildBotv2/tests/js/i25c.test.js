// Lane i25c (D123): a job with no app never reads "working", on the Desktop or in the window. Run by
// tests/test_i25c_library_job_words.py, with a file of the Core's own views from the real library proof.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const WORDS = "No app yet, your library has some reading on this";
const lib = (name) => ({ id: name, name, status: "working", library: true });
const app = (name) => ({ id: name, name, status: "working" });
const site = (name) => ({ id: name, name, status: "working" });     // a free site: `working`, no `library`
const newest = (id, jobs, goal) => ({ builds: [{ id, goal, created_at: "2026-10-31T10:00:00", jobs }] });
const GOALS = [["a home recipe bot", "Recipe guides"], ["a small-business invoices bot", "Send invoices"], ["a birdwatching bot", "Bird ID guides"]];

// -- one job
assert.strictEqual(L.jobWords("working", true), WORDS);
assert.strictEqual(L.jobWords("working"), "Working");
assert.strictEqual(L.jobWords("not_yet", true), "Not working yet");   // the flag means nothing on another state
assert.strictEqual(L.jobGood(lib("x")), false);
assert.strictEqual(L.jobGood(app("x")), true);
assert.strictEqual(L.jobGood(site("x")), true);
assert.strictEqual(L.jobGood({ status: "not_yet" }), false);

GOALS.forEach(([goal, name]) => {
  // -- buildView: the job's words, the Building page and the window read these
  const v = L.buildView({ id: "b", goal, yes: { given: true }, jobs: [{ id: "j", name, status: "working", library: true, run: { state: "working", line: "x", result: { by: "knowledge" } } }] });
  assert.strictEqual(v.jobs[0].words, WORDS);
  assert.strictEqual(v.jobs[0].library, true);
  assert.strictEqual(L.jobGood(v.jobs[0]), false);
  assert.strictEqual(v.canAgain, false);                     // Check again as before: nothing to try for it

  // -- a build of only such jobs: the rail, Home's list, the line on Home and Building
  const row = newest("b", [lib(name), lib(name + " 2")], goal);
  const chip = L.buildChip(row.builds[0], false);
  assert.strictEqual(chip.words, "no apps yet");
  assert.notStrictEqual(chip.tone, "ok");
  assert.strictEqual(L.buildStopped(row), null);             // it did not stop: nothing failed
  const r = L.buildReading(row);
  assert.strictEqual(r.head, "Your last build has no apps yet. Your library has some reading on this.");
  assert.ok(/ask the chat/i.test(r.next) && /GitHub address/.test(r.next));
  assert.strictEqual(r.link, "#building/b");
  assert.ok(!/\bworking\b|\bdone\b|\bworks\b/i.test(r.head + r.next + chip.words));
});

// -- Home's live list keeps the words too
const liveNow = L.buildingNow(newest("b", [lib("A"), { id: "B", name: "B", status: "building" }], "g"));
assert.strictEqual(liveNow.live[0].jobs[0].words, WORDS);

// -- a real app: as it was
const real = newest("b", [app("Pantry")], "g");
assert.deepStrictEqual(L.buildChip(real.builds[0], false), { words: "done", tone: "ok" });
assert.strictEqual(L.buildReading(real), null);

// -- a free site stand-in: never called a library job
const free = newest("b", [site("Recipes")], "g");
assert.deepStrictEqual(L.buildChip(free.builds[0], false), { words: "done", tone: "ok" });
assert.strictEqual(L.buildReading(free), null);

// -- mixed builds say what is true
const mix = newest("b", [app("Pantry"), lib("Bird ID")], "g");
const mixChip = L.buildChip(mix.builds[0], false);
assert.strictEqual(mixChip.words, "some have no app");
assert.notStrictEqual(mixChip.tone, "ok");
assert.strictEqual(L.buildReading(mix).head, "1 job of your last build has no app yet. Your library has some reading on it.");
assert.strictEqual(L.buildReading(newest("b", [app("A"), lib("B"), lib("C")], "g")).head,
  "2 jobs of your last build have no app yet. Your library has some reading on them.");

const stopped = newest("b", [lib("Bird ID"), { id: "x", name: "Invoices", status: "not_yet" }], "g");
assert.deepStrictEqual(L.buildChip(stopped.builds[0], false), { words: "stopped", tone: "stop" });
const s = L.buildStopped(stopped);
assert.strictEqual(s.count, 1);
assert.strictEqual(s.head, "Your last build stopped with 1 job not working yet. 1 job has no app yet, your library has some reading on it.");
assert.strictEqual(L.buildReading(stopped), null);          // said by the stopped line, once

// A build still going: nothing said yet.
assert.strictEqual(L.buildReading(newest("b", [lib("A"), { id: "B", name: "B", status: "building" }], "g")), null);

// -- Check again on a mixed build: true about the job it leaves alone
const again = L.buildView({ id: "b", yes: { given: true }, jobs: [
  { id: "a", name: "A", status: "working", library: true, run: {} }, { id: "c", name: "C", status: "not_yet", run: {} }] });
assert.strictEqual(again.canAgain, true);
assert.strictEqual(again.unfinished, 1);
assert.strictEqual(L.againMore(again), " A job with only reading from your library stays as it is too.");
assert.strictEqual(L.againMore(L.buildView({ id: "b", yes: { given: true }, jobs: [{ id: "c", name: "C", status: "not_yet", run: {} }] })), "");

// -- the Core's own views, from the real library proof
const file = process.argv[2];
if (file) {
  const d = JSON.parse(fs.readFileSync(file, "utf-8"));
  const row = (id) => d.status.builds.find((b) => b.id === id);
  Object.values(d.goals).forEach((id) => {
    const v = L.buildView(d.views[id]);
    assert.strictEqual(v.jobs[0].status, "working");
    assert.strictEqual(v.jobs[0].words, WORDS);
    assert.strictEqual(L.jobGood(v.jobs[0]), false);
    assert.ok(!/has it\b/.test(v.jobs[0].line));
    assert.strictEqual(L.buildChip(row(id), false).words, "no apps yet");
  });
  assert.strictEqual(L.buildChip(row(d.mixed), false).words, "some have no app");
  assert.deepStrictEqual(L.buildChip(row(d.app), false), { words: "done", tone: "ok" });
  assert.deepStrictEqual(L.buildChip(row(d.site), false), { words: "done", tone: "ok" });
  const sv = L.buildView(d.views[d.site]);
  assert.strictEqual(sv.jobs[0].words, "Working");
  assert.strictEqual(L.jobGood(sv.jobs[0]), true);
}
console.log("ok");
