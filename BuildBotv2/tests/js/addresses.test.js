// The "Addresses you allowed for packages" block (packages/desktop/web/addresses.js), with node. Prints "ok".
// A small stand-in for window.Puff keeps what the block draws as plain objects, so a test reads every word it would show.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "..", "..", "packages", "desktop", "web", "addresses.js");
const A = require(file);
const source = fs.readFileSync(file, "utf8");

function node(tag, attrs, kids) {
  const n = { tag, attrs: attrs || {}, kids: [], text: (attrs || {}).text, value: "", listeners: {}, disabled: false };
  Object.defineProperty(n, "textContent", { get() { return n.text || ""; }, set(v) { n.text = String(v); n.kids = []; } });
  n.appendChild = (c) => { n.kids.push(c); return c; };
  n.append = (...cs) => { cs.forEach((c) => n.kids.push(c)); };
  n.addEventListener = (ev, fn) => { n.listeners[ev] = fn; };
  Object.entries((attrs || {}).on || {}).forEach(([ev, fn]) => { n.listeners[ev] = fn; });
  (kids || []).forEach((c) => { if (c) n.kids.push(c); });
  return n;
}
const walk = (n, fn) => { if (n && typeof n === "object") { fn(n); n.kids.forEach((c) => walk(c, fn)); } };
const words = (n) => { const out = []; const go = (x) => { if (typeof x === "string") out.push(x); else if (x) { if (x.text) out.push(x.text); x.kids.forEach(go); } }; go(n); return out.join(" "); };
const find = (root, name, value) => { let hit = null; walk(root, (n) => { if (!hit && n.attrs[name] === value) hit = n; }); return hit; };
const findAll = (root, name) => { const out = []; walk(root, (n) => { if (n.attrs[name] !== undefined) out.push(n); }); return out; };
const byTag = (root, tag) => { const out = []; walk(root, (n) => { if (n.tag === tag) out.push(n); }); return out; };
const flush = () => new Promise((ok) => setImmediate(ok));

function fakePuff(replies) {
  const calls = [], inline = [];
  const P = {
    calls, inline,
    el: node,
    clear(n) { n.kids = []; n.text = undefined; return n; },
    icon: (name) => node("span", { text: name }),
    tile(o) { const body = node("div"); return { node: node("section", { "data-tile": o.id }, [node("h2", { text: o.title }), body]), body }; },
    later: (what) => node("div", { text: "later: " + what }),
    missing: (r) => r.status === 404 && !r.data,
    said: (r, fallback) => (r.data && (r.data.error || r.data.line)) || fallback || "That did not work.",
    consentInline(box, id, after) { inline.push({ box, id, after }); },
    async api(method, route, body) {
      calls.push({ method, route, body });
      const fn = replies[method + " " + route] || replies[method];
      return fn ? fn(body) : { status: 500, data: { error: "no reply set" } };
    },
  };
  return P;
}

const HOSTILE = "<img src=x onerror=alert(1)>";
const ROWS = [
  { consent_id: "c_on", package: "lamp-tool", name: "Lamps", address: "192.168.1.20", port: 8123, where: "lan", where_said: "home network", state: "on", line: "On. Only Lamps may reach this." },
  { consent_id: "c_web", package: "news-tool", name: "News", address: "news.example.com", port: 443, where: "internet", where_said: "internet", state: "waiting", line: "Waiting: the Bridge is closed, so this works once you open it." },
  { consent_id: "c_bad", package: "bad-tool", name: HOSTILE, address: "printer.local", port: 631, where: "lan", where_said: HOSTILE, state: HOSTILE, line: HOSTILE, description: "from the package", consent: "from the package" },
];

(async () => {
  // what she types is checked plainly, and the body is only the package and the address, trimmed at the ends
  assert.ok(/package/i.test(A.formProblem({ package: "", address: "x:1" })));
  assert.ok(/address/i.test(A.formProblem({ package: "a-tool", address: "  " })));
  assert.strictEqual(A.formProblem({ package: "a-tool", address: "x:1" }), "");
  assert.deepStrictEqual(A.formBody({ package: " a-tool ", address: " 192.168.1.20:8123 ", other: "y", grant: true, consent_id: "c" }),
    { package: "a-tool", address: "192.168.1.20:8123" });

  // a row says home network or internet, on or waiting, from fixed words: never from what a reply claims
  const on = A.rowView(ROWS[0]), web = A.rowView(ROWS[1]), bad = A.rowView(ROWS[2]);
  assert.deepStrictEqual([on.where, on.state, on.stateWord, on.address, on.port], ["home network", "on", "On", "192.168.1.20", "8123"]);
  assert.deepStrictEqual([web.where, web.state, web.stateWord], ["internet", "waiting", "Waiting"]);
  assert.deepStrictEqual([bad.where, bad.state, bad.stateWord], ["home network", "waiting", "Waiting"]);
  assert.ok(!("description" in bad) && !("consent" in bad));
  assert.strictEqual(A.rowView({ port: 0 }).port, "0");
  assert.strictEqual(A.rowView({}).port, "");

  // what a POST answered
  assert.deepStrictEqual(A.replyView({ status: 202, data: { consent_id: "c_1", line: "Its Yes card is below." } }), { ok: true, consentId: "c_1", line: "Its Yes card is below." });
  assert.deepStrictEqual(A.replyView({ status: 200, data: { state: "not_needed", line: "Not needed." } }), { ok: true, consentId: "", line: "Not needed." });
  assert.deepStrictEqual(A.replyView({ status: 400, data: { consent_id: "c_x", error: "Refused." } }), { ok: false, consentId: "", line: "Refused." });
  assert.strictEqual(A.replyView({ status: 202, data: { consent_id: 7 } }).consentId, "");
  assert.strictEqual(A.replyView(null).ok, false);

  // the page never writes HTML, and has no way to answer a card or keep anything in the browser
  ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage", "sessionStorage", "console."].forEach((bad_) =>
    assert.ok(!source.includes(bad_), bad_));
  assert.ok(!/\/v1\/consent/.test(source), "this file never touches the consent routes");
  assert.ok(!source.includes(String.fromCharCode(0x2014)) && !source.includes(String.fromCharCode(9)));
  assert.ok(/Addresses you allowed for packages/.test(A.TITLE) && A.ALLOW === "Allow this address and port");

  // the block draws every row, by its own fixed words and as text
  const P = fakePuff({
    "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: ROWS, setting: "open", note: "", packages: [{ id: "lamp-tool", name: "Lamps" }, { id: "bad-tool", name: HOSTILE }] } }),
  });
  const shown = A.block(P);
  await flush();
  assert.strictEqual(shown.attrs["data-tile"], "addresses");
  const rows = findAll(shown, "data-address-row");
  assert.deepStrictEqual(rows.map((r) => r.attrs["data-address-row"]), ["c_on", "c_web", "c_bad"]);
  assert.deepStrictEqual(rows.map((r) => r.attrs["data-address-state"]), ["on", "waiting", "waiting"]);
  assert.deepStrictEqual(rows.map((r) => find(r, "data-address", "where").text), ["home network", "internet", "home network"]);
  assert.deepStrictEqual(rows.map((r) => find(r, "data-address", "state").text), ["On. ", "Waiting. ", "Waiting. "]);
  const first = words(rows[0]);
  ["Lamps", "192.168.1.20", "8123", "home network", "On.", "Remove"].forEach((w) => assert.ok(first.includes(w), w));
  assert.ok(words(rows[1]).includes("news.example.com") && words(rows[1]).includes("443") && words(rows[1]).includes("internet"));
  // a hostile name reaches the page only as the text of a text node, and is never put in an attribute; its other fields never at all
  const hostile = [];
  walk(shown, (n) => {
    Object.entries(n.attrs).forEach(([k, v]) => { if (k !== "text" && String(v).includes("onerror")) hostile.push(k); });
    n.kids.forEach((c) => { if (typeof c === "string" && c.includes("onerror")) hostile.push("kid"); });
  });
  assert.deepStrictEqual(hostile.filter((h) => h !== "kid"), []);
  assert.ok(!words(shown).includes("from the package"));
  // the picker: installed packages, by name and id
  const options = byTag(shown, "option");
  assert.deepStrictEqual(options.map((o) => o.attrs.value), ["lamp-tool", "bad-tool"]);
  assert.strictEqual(options[0].text, "Lamps (lamp-tool)");

  // the form raises a card and shows it inline: one POST, no answer, no grant
  const asked = fakePuff({
    "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: [], setting: "closed", packages: [{ id: "lamp-tool", name: "Lamps" }] } }),
    "POST /v1/bridge/addresses": () => ({ status: 202, data: { state: "waiting", consent_id: "c_new", card_sha: "ab", title: "t", lines: ["l"], line: "Its Yes card is below, and on Your turn." } }),
  });
  const block2 = A.block(asked);
  await flush();
  assert.ok(words(block2).includes("None allowed yet."));
  const form = find(block2, "data-address", "form");
  const input = byTag(block2, "input")[0];
  const pick = byTag(block2, "select")[0];
  const said = find(block2, "data-address", "form-said");
  const submit = () => form.listeners.submit({ preventDefault() {} });
  // nothing picked or nothing typed: nothing is sent
  pick.value = "";
  input.value = "192.168.1.20:8123";
  await submit();
  assert.ok(/package/i.test(said.text));
  assert.strictEqual(asked.calls.filter((c) => c.method === "POST").length, 0);
  pick.value = "lamp-tool";
  input.value = "   ";
  await submit();
  assert.ok(/address/i.test(said.text));
  assert.strictEqual(asked.calls.filter((c) => c.method === "POST").length, 0);
  input.value = " 192.168.1.20:8123 ";
  await submit();
  await flush();
  const posts = asked.calls.filter((c) => c.method === "POST");
  assert.deepStrictEqual(posts.map((c) => [c.route, c.body]), [["/v1/bridge/addresses", { package: "lamp-tool", address: "192.168.1.20:8123" }]]);
  assert.deepStrictEqual(asked.inline.map((x) => x.id), ["c_new"]);
  assert.strictEqual(find(block2, "data-address", "card"), asked.inline[0].box, "the card is shown right there");
  assert.strictEqual(input.value, "");
  assert.ok(said.text.includes("Yes card"));
  assert.ok(!asked.calls.some((c) => /consent|answer/.test(c.route)), "the form never answers its own card");
  // her answer is the card's own; the block only draws again
  const gets = asked.calls.filter((c) => c.method === "GET").length;
  asked.inline[0].after("yes");
  await flush();
  assert.strictEqual(asked.calls.filter((c) => c.method === "GET").length, gets + 1);

  // a refusal shows the Core's line and raises no card, and what she typed stays to fix
  const refused = fakePuff({
    "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: [], packages: [{ id: "lamp-tool", name: "Lamps" }] } }),
    "POST /v1/bridge/addresses": () => ({ status: 400, data: { state: "refused", error: "That address can't be allowed." } }),
  });
  const block3 = A.block(refused);
  await flush();
  const input3 = byTag(block3, "input")[0];
  byTag(block3, "select")[0].value = "lamp-tool";
  input3.value = "169.254.169.254:80";
  await find(block3, "data-address", "form").listeners.submit({ preventDefault() {} });
  assert.strictEqual(refused.inline.length, 0);
  assert.strictEqual(find(block3, "data-address", "form-said").text, "That address can't be allowed.");
  assert.strictEqual(input3.value, "169.254.169.254:80");

  // not needed: a line and no card
  const local = fakePuff({
    "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: [], packages: [{ id: "lamp-tool", name: "Lamps" }] } }),
    "POST /v1/bridge/addresses": () => ({ status: 200, data: { state: "not_needed", line: "Not needed: a service on this computer needs no Bridge step." } }),
  });
  const block4 = A.block(local);
  await flush();
  byTag(block4, "select")[0].value = "lamp-tool";
  byTag(block4, "input")[0].value = "localhost:8123";
  await find(block4, "data-address", "form").listeners.submit({ preventDefault() {} });
  assert.strictEqual(local.inline.length, 0);
  assert.ok(/Not needed/.test(find(block4, "data-address", "form-said").text));

  // Remove: one DELETE on that row, then the list again; a failure stays on the row
  let list = [ROWS[0]];
  const removing = fakePuff({
    "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: list, packages: [] } }),
    "DELETE /v1/bridge/addresses/c_on": () => { list = []; return { status: 200, data: { deleted: true, line: "Removed. This package can't reach that address any more." } }; },
  });
  const block5 = A.block(removing);
  await flush();
  await find(block5, "data-address", "remove").listeners.click();
  await flush();
  const dels = removing.calls.filter((c) => c.method === "DELETE");
  assert.deepStrictEqual(dels.map((c) => c.route), ["/v1/bridge/addresses/c_on"]);
  assert.strictEqual(findAll(block5, "data-address-row").length, 0);
  assert.ok(/Removed/.test(find(block5, "data-address", "note").text));
  list = [Object.assign({}, ROWS[0], { consent_id: "c x/y" })];
  const failing = fakePuff({ "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: list, packages: [] } }),
    "DELETE /v1/bridge/addresses/c%20x%2Fy": () => ({ status: 409, data: { error: "That Yes could not be taken back." } }) });
  const block6 = A.block(failing);
  await flush();
  await find(block6, "data-address", "remove").listeners.click();
  await flush();
  assert.strictEqual(find(block6, "data-address", "said").text, "That Yes could not be taken back.");
  assert.strictEqual(findAll(block6, "data-address-row").length, 1);

  // a reply it can't read draws no rows, and says so; a route not in this build says it is for later
  const broken = fakePuff({ "GET /v1/bridge/addresses": () => ({ status: 200, data: { addresses: "nope", note: "PuffPAI can't protect its own settings.", packages: null } }) });
  const block7 = A.block(broken);
  await flush();
  assert.strictEqual(findAll(block7, "data-address-row").length, 0);
  assert.ok(/can't protect/.test(find(block7, "data-address", "note").text));
  const old = fakePuff({ "GET /v1/bridge/addresses": () => ({ status: 404, data: null }) });
  const block8 = A.block(old);
  await flush();
  assert.ok(/later/.test(words(block8)));

  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
