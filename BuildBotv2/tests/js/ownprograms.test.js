// The "A program on this computer" tile (packages/desktop/web/ownprograms.js), with node and a small stand-in for the page.
// Prints "ok".
const assert = require("assert");
const path = require("path");

function node(tag) {
  const n = { tag: tag, kids: [], attrs: {}, on: {}, textContent: "", value: "", hidden: false, checked: false, selectedIndex: 0,
    append: function (...k) { k.forEach((c) => { if (c) n.kids.push(c); }); },
    addEventListener: function (ev, fn) { n.on[ev] = fn; },
    get options() { return n.kids; } };
  return n;
}
function el(tag, attrs, kids) {
  const n = node(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "text") n.textContent = v;
    else if (k === "on") Object.entries(v).forEach(([ev, fn]) => { n.on[ev] = fn; });
    else if (k === "hidden") n.hidden = true;
    else if (k === "checked") n.checked = true;
    else if (k === "value") n.value = v;
    else n.attrs[k] = v;
  });
  (kids || []).forEach((c) => { if (c) n.kids.push(c); });
  return n;
}
function find(n, want, out) {
  out = out || [];
  if (n && want(n)) out.push(n);
  (n && n.kids || []).forEach((k) => find(k, want, out));
  return out;
}
const own = (n, name) => find(n, (x) => x.attrs && x.attrs["data-own"] === name);
const tick = () => new Promise((ok) => setImmediate(ok));

const calls = [];
let rows = [
  { id: "p11111111", name: "ffmpeg", kind: "cli", path: "/x/ffmpeg", command: "ffmpeg", always_allow: false, commands: [{ command: "ffmpeg convert" }] },
  { id: "p22222222", name: "Letters", kind: "desktop", path: "/x/Letters.exe", command: "", always_allow: false, commands: [] },
];
const replies = {
  "GET /v1/programs": () => ({ status: 200, data: { programs: rows, installed_desktop: [{ name: "Letters", path: "/X/LETTERS.EXE" }, { name: "Sheets", path: "/x/Sheets.exe" }] } }),
};
const body = node("div");
global.window = {
  Puff: {
    el: el,
    clear: (n) => { n.kids = []; return n; },
    tile: () => ({ node: body, body: body }),
    said: (r) => (r.data && r.data.error) || "No.",
    api: async (method, url, sent) => {
      calls.push([method, url, sent]);
      const fn = replies[method + " " + url];
      return fn ? fn(sent) : { status: 200, data: {} };
    },
  },
};
const O = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "ownprograms.js"));

// a slash means a path; anything else a name on PATH
assert.deepStrictEqual(O.addBody("cli", " ffmpeg "), { kind: "cli", command: "ffmpeg" });
assert.deepStrictEqual(O.addBody("cli", "C:\\Tools\\my.exe"), { kind: "cli", path: "C:\\Tools\\my.exe" });
assert.deepStrictEqual(O.addBody("desktop", "/a/b.app", "B"), { kind: "desktop", path: "/a/b.app", name: "B" });
// a desktop program opens; a command line one has the switch
assert.strictEqual(O.rowView(rows[0]).canAlways, true);
assert.strictEqual(O.rowView(rows[0]).open, false);
assert.strictEqual(O.rowView(rows[1]).open, true);
assert.strictEqual(O.rowView(rows[1]).canAlways, false);
// what is added already is not offered again
assert.deepStrictEqual(O.notAdded([{ name: "L", path: "/X/LETTERS.EXE" }, { name: "S", path: "/s" }], rows).map((a) => a.name), ["S"]);

(async function () {
  O.block();
  await tick();
  await tick();
  // the list, the switch only on the command line one, Open only on the desktop one, Remove on both
  assert.strictEqual(own(body, "always").length, 1);
  assert.strictEqual(own(body, "open").length, 1);
  assert.strictEqual(own(body, "remove").length, 2);
  assert.deepStrictEqual(own(body, "installed")[0].kids.map((o) => o.textContent), ["Sheets"]);

  // the switch sends her choice and shows the Core's warning
  replies["PATCH /v1/programs/p11111111"] = () => ({ status: 200, data: { warning: "This program can start any other program." } });
  const box = own(body, "always")[0];
  box.checked = true;
  box.on.change();
  await tick();
  assert.deepStrictEqual(calls[calls.length - 2 >= 0 ? calls.findIndex((c) => c[0] === "PATCH") : 0][2], { always_allow: true });
  await tick();
  await tick();
  assert.strictEqual(own(body, "said")[0].textContent, "This program can start any other program.");

  // Open and Remove go to their own routes
  own(body, "open")[0].on.click();
  await tick();
  assert.ok(calls.some((c) => c[0] === "POST" && c[1] === "/v1/programs/p22222222/launch"));
  await tick();
  own(body, "remove")[0].on.click();
  await tick();
  assert.ok(calls.some((c) => c[0] === "DELETE" && c[1] === "/v1/programs/p11111111"));
  await tick();

  // Add: a refusal keeps what she typed and says why
  replies["POST /v1/programs"] = () => ({ status: 400, data: { error: "There is no program at that place." } });
  const input = find(body, (n) => n.tag === "input" && n.attrs.type === "text")[0];
  input.value = "C:\\nope.exe";
  own(body, "add")[0].on.click();
  await tick();
  assert.strictEqual(own(body, "said")[0].textContent, "There is no program at that place.");
  assert.strictEqual(input.value, "C:\\nope.exe");
  replies["POST /v1/programs"] = () => ({ status: 200, data: { program: { name: "tally" } } });
  own(body, "add")[0].on.click();
  await tick();
  await tick();
  await tick();
  assert.strictEqual(input.value, "");
  assert.strictEqual(own(body, "said")[0].textContent, "Added tally.");

  // an app from the list of what is on this computer
  const before = calls.length;
  const picks = own(body, "installed")[0];
  picks.selectedIndex = 0;
  own(body, "add-installed")[0].on.click();
  await tick();
  const sent = calls.slice(before).find((c) => c[0] === "POST" && c[1] === "/v1/programs")[2];
  assert.strictEqual(sent.kind, "desktop");
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
