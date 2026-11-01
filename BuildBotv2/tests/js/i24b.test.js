// D113 (lane i24b): the words for a build waiting on a locked vault, a long step's sign of life and a failed poll
// (packages/desktop/web/logic.js), with node. Prints "ok".
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const at = Date.parse("2026-10-31T06:12:00+00:00");
const since = "2026-10-31T06:00:00+00:00";

for (const name of ["Home recipe box", "Small-business invoices", "Birdwatching log"]) {
  // a job waiting on the vault is found in GET /v1/toolsmith's rows, in any build
  const status = { builds: [{ id: "b0", jobs: [{ name: "Old", status: "working" }] },
                            { id: "b1", jobs: [{ name, status: "building: X" }, { name, status: "waiting_vault" }] }] };
  assert.strictEqual(L.vaultWaiting(status), "b1");
  assert.strictEqual(L.vaultWaiting({ builds: [{ id: "b1", jobs: [{ name, status: "building: X" }] }] }), "");
  assert.strictEqual(L.vaultWaiting({ builds: [] }), "");
  assert.strictEqual(L.vaultWaiting(null), "");

  // only while it builds, and from what the job already says
  const j = { name, status: "building: An app", now: "An app (1 of 2): step 4 of at most 20: running.", since };
  assert.strictEqual(L.stillWorking(j, at), "Still working: An app (1 of 2): step 4 of at most 20: running, 12 minutes so far.");
  assert.strictEqual(L.stillWorking(j, at, true), "Still working: " + name + ": An app (1 of 2): step 4 of at most 20: running, 12 minutes so far.");
  assert.strictEqual(L.stillWorking(Object.assign({}, j, { now: "" }), at), "Still working: An app, 12 minutes so far.");
  assert.strictEqual(L.stillWorking({ name, status: "building", since }, at), "Still working: " + name + ", 12 minutes so far.");
  for (const other of ["waiting_vault", "waiting_you", "planned", "working", "held"]) {
    assert.strictEqual(L.stillWorking(Object.assign({}, j, { status: other }), at), "");
  }
}

// a minute, under a minute, a time that can't be read, a clock behind
const one = { name: "J", status: "building: A", now: "n", since: "2026-10-31T06:11:00+00:00" };
assert.strictEqual(L.stillWorking(one, at), "Still working: n, 1 minute so far.");
assert.strictEqual(L.stillWorking(Object.assign({}, one, { since: "2026-10-31T06:11:30+00:00" }), at), "");
assert.strictEqual(L.stillWorking(Object.assign({}, one, { since: "" }), at), "");
assert.strictEqual(L.stillWorking(Object.assign({}, one, { since: "not a time" }), at), "");
assert.strictEqual(L.stillWorking(Object.assign({}, one, { since: "2026-10-31T07:00:00+00:00" }), at), "");

// the build view hands over what these need
const v = L.buildView({ id: "b1", yes: { given: true }, now: "Planning.", now_at: since, planning: true,
                        jobs: [{ id: "b1-1", name: "J", status: "building: A", run: { state: "building", trying: "A", now: "n", since }, plan: {} }] });
assert.strictEqual(v.nowAt, since);
assert.strictEqual(v.jobs[0].since, since);
assert.strictEqual(L.stillWorking(v.jobs[0], at), "Still working: n, 12 minutes so far.");
assert.strictEqual(L.stillWords(v.now, v.nowAt, at), "Still working: Planning, 12 minutes so far.");

// the Setup page's one place
assert.deepStrictEqual(L.setupAlive(null, at), { vault: false, lines: [] });
assert.deepStrictEqual(L.setupAlive({ vault: "yes" }, at), { vault: false, lines: [] });
assert.deepStrictEqual(L.setupAlive({ vault: true, jobs: [{ name: "A", status: "waiting_vault", now: "stale", since },
                                                          { name: "B", status: "building: X", now: "n", since }] }, at),
                       { vault: true, lines: ["Still working: B: n, 12 minutes so far."] });

// a poll goes again when nothing answered or the Core failed; never on a plain refusal or "not built"
for (const s of [0, 500, 502, 503]) assert.strictEqual(L.pollCanRetry(s), true);
for (const s of [200, 400, 401, 403, 404, 409, 501]) assert.strictEqual(L.pollCanRetry(s), false);
assert.ok(L.POLL_WAIT_MS >= 10000);

// plain words, no em dash, no raw state word
for (const t of [L.VAULT_WAIT, L.VAULT_WAIT_HOME, L.VAULT_WAIT_WINDOW, L.POLL_RETRY]) {
  assert.ok(typeof t === "string" && t.length > 10);
  assert.ok(t.indexOf("\u2014") < 0);
  assert.ok(!/waiting_vault|timed? ?out/i.test(t));
}
assert.ok(L.VAULT_WAIT_WINDOW.indexOf("PIN") >= 0 && L.VAULT_WAIT_HOME.indexOf("Building") >= 0);

// -- the real poll code of the two pages, cut out of their files, with no DOM and no browser
const fs = require("fs");
const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");
function cut(file, from, to) {
  const s = fs.readFileSync(path.join(web, file), "utf8");
  const a = s.indexOf(from), b = s.indexOf(to, a);
  assert.ok(a >= 0 && b > a, from);
  return s.slice(a, b);
}
const tick = () => new Promise((ok) => setImmediate(ok));

(async () => {
  // the dashboard's Setup page
  const setup = (answers) => {
    const w = { timers: [], sent: [], drawn: [], added: [], gone: 0, answers,
                S: { step: "build", view: "setup" } };
    w.wiz = { querySelectorAll: () => ({ forEach: () => { w.gone++; } }), appendChild: (n) => w.added.push(n) };
    const code = cut("desktop.js", "async function send(answers, poll)", "function drawScreen(sc)");
    const api = async (m, p, body, waitMs) => { w.sent.push({ body, waitMs }); return w.answers.shift(); };
    const run = new Function("S", "L", "api", "$", "el", "said", "drawScreen", "setTimeout", "clearTimeout",
      code + "; return { send, pollSoon };");
    return Object.assign(w, run(w.S, L, api, () => w.wiz, (t, a) => a, () => "refused", (sc) => w.drawn.push(sc),
      (f, ms) => { w.timers.push({ f, ms }); return w.timers.length; }, () => {}));
  };
  // one failed poll is not the end: it says so, and goes again at its usual pace
  let w = setup([{ status: 0, data: {} }, { status: 503, data: {} }, { status: 200, data: { status: "working" } }]);
  await w.send({}, true);
  assert.deepStrictEqual(w.added.map((n) => n.text), [L.POLL_RETRY]);
  assert.strictEqual(w.sent[0].waitMs, L.POLL_WAIT_MS);
  assert.strictEqual(w.timers.length, 1);
  assert.strictEqual(w.timers[0].ms, 2000);
  w.timers.pop().f();
  await tick();
  assert.strictEqual(w.sent.length, 2);
  w.timers.pop().f();
  await tick();
  assert.strictEqual(w.drawn.length, 1);
  // a plain refusal ends it, and so does a failed answer, which is never sent twice by itself
  w = setup([{ status: 404, data: {} }]);
  await w.send({}, true);
  assert.strictEqual(w.timers.length, 0);
  w = setup([{ status: 0, data: {} }]);
  await w.send({ card: "yes" });
  assert.strictEqual(w.timers.length, 0);
  assert.deepStrictEqual(w.added.map((n) => n.text), ["refused"]);
  assert.ok(!w.sent[0].waitMs);
  // away from the page its poll parks; another step's answer is dropped
  w = setup([]);
  w.pollSoon("build", 2);
  w.S.view = "home";
  w.timers.pop().f();
  assert.strictEqual(w.S.pollParked, "build");
  assert.strictEqual(w.sent.length, 0);
  w = setup([{ status: 0, data: {} }]);
  const going = w.send({}, true);
  w.S.step = "done";
  await going;
  assert.strictEqual(w.timers.length, 0);
  assert.strictEqual(w.added.length, 0);
  assert.ok(cut("desktop.js", "async function loadWizard", "function drawSteps").indexOf("S.pollParked === S.step") > 0);   // back: it goes on

  // the window: once per wait, Building beside the chat; it keeps looking while it waits, and after a failed poll
  const win = (answers) => {
    const w2 = { timers: [], said: [], tabs: 0, redraws: 0, answers, S: { waiting: new Set() } };
    const run = new Function("S", "L", "api", "say", "currentView", "openTab", "buildingTab", "redrawTab", "drawRailBuilds", "renderTabs", "setTimeout", "clearTimeout", "drawWaiting",
      cut("window.js", "async function loadTool()", "async function refreshCounts()") + "; return loadTool;");
    w2.load = run(w2.S, L, async () => w2.answers.shift(), (v, t) => w2.said.push(t), () => "v", () => { w2.tabs++; }, () => ({}), () => { w2.redraws++; },
      () => {}, () => {}, (f) => { w2.timers.push(f); return w2.timers.length; }, () => {}, () => {});
    return w2;
  };
  const row = (status) => ({ status: 200, data: { builds: [{ id: "b1", jobs: [{ name: "Birdwatching log", status }] }] } });
  const x = win([row("waiting_vault"), row("waiting_vault"), row("building: A"), row("waiting_vault"), row("working"), { status: 0, data: {} }, { status: 403, data: {} }]);
  await x.load();
  assert.deepStrictEqual(x.said, [L.VAULT_WAIT_WINDOW]);
  assert.strictEqual(x.tabs, 1);
  assert.strictEqual(x.timers.length, 1);               // today's chip says "waiting on you", and used to end the poll here
  await x.load();
  assert.strictEqual(x.said.length, 1);
  await x.load();                                       // she unlocked
  await x.load();                                       // it locked again
  assert.strictEqual(x.said.length, 2);
  assert.strictEqual(x.tabs, 2);
  const before = x.timers.length;
  await x.load();                                       // every job has ended: the poll ends
  assert.strictEqual(x.timers.length, before);
  await x.load();                                       // no answer: it goes again
  assert.strictEqual(x.timers.length, before + 1);
  await x.load();                                       // a refusal: it does not
  assert.strictEqual(x.timers.length, before + 1);
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
