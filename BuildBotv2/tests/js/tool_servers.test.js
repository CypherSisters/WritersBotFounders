// The "Add my own tool" block (packages/desktop/web/tool_servers.js, D137), with node. Prints "ok".
// A small stand-in for window.Puff keeps what the block draws as plain objects, so a test reads every word it would show.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const file = path.join(__dirname, "..", "..", "packages", "desktop", "web", "tool_servers.js");
const T = require(file);
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
  return {
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
      calls.push({ method, route, body: body ? JSON.parse(JSON.stringify(body)) : body });
      const fn = replies[method + " " + route] || replies[method];
      return fn ? fn(body) : { status: 500, data: { error: "no reply set" } };
    },
  };
}

const HOSTILE = "<img src=x onerror=alert(1)>";
const ROWS = [
  { id: "my_tools", name: "My tools", url: "http://127.0.0.1:8000/mcp", address: "127.0.0.1:8000", where: "here", where_said: "this computer", has_password: true, tool_count: 2, tools: [{ name: "a" }, { name: "b" }], state: "ready", line: "Ready. 2 tools for the brain." },
  { id: "lamps", name: "Lamps", url: "http://192.168.1.20:8123/mcp", address: "192.168.1.20:8123", where: "lan", where_said: "home network", has_password: false, tool_count: 0, tools: [], state: "waiting", line: "Waiting for your Yes on the Bridge page for 192.168.1.20:8123. Nothing connects before that." },
  { id: "bad", name: HOSTILE, url: HOSTILE, address: HOSTILE, where_said: HOSTILE, tool_count: 1, state: HOSTILE, line: HOSTILE, password: "from the reply" },
];

(async () => {
  // what she types is checked plainly; the body is the name, the address and a password only when one was typed
  assert.ok(/name/i.test(T.formProblem({ name: " ", address: "http://127.0.0.1:1/mcp" })));
  assert.ok(/address/i.test(T.formProblem({ name: "a", address: "" })));
  assert.ok(/http/i.test(T.formProblem({ name: "a", address: "python server.py" })));
  assert.strictEqual(T.formProblem({ name: "a", address: "https://tools.example.com/mcp" }), "");
  assert.deepStrictEqual(T.formBody({ name: " a ", address: " http://127.0.0.1:1/mcp ", password: "  ", other: 1 }), { name: "a", address: "http://127.0.0.1:1/mcp" });
  assert.deepStrictEqual(T.formBody({ name: "a", address: "http://x.example.com/", password: " pw1 " }), { name: "a", address: "http://x.example.com/", password: "pw1" });

  // a row: ready or waiting from fixed words, and never a password
  const ready = T.rowView(ROWS[0]), waiting = T.rowView(ROWS[1]), bad = T.rowView(ROWS[2]);
  assert.deepStrictEqual([ready.state, ready.stateWord, ready.toolsWord, ready.password], ["ready", "Ready", "2 tools", true]);
  assert.deepStrictEqual([waiting.state, waiting.stateWord, waiting.toolsWord, waiting.where], ["waiting", "Waiting", "0 tools", "home network"]);
  assert.deepStrictEqual([bad.state, bad.toolsWord, bad.password], ["waiting", "1 tool", false]);
  assert.ok(!JSON.stringify(bad).includes("from the reply"));

  // what a POST answered
  assert.deepStrictEqual(T.replyView({ status: 202, data: { consent_id: "c_1", line: "Waiting for your Yes." } }), { ok: true, consentId: "c_1", line: "Waiting for your Yes." });
  assert.deepStrictEqual(T.replyView({ status: 502, data: { consent_id: "c_x", error: "Could not reach it. Nothing was kept." } }), { ok: false, consentId: "", line: "Could not reach it. Nothing was kept." });
  assert.strictEqual(T.replyView(null).ok, false);

  // the page never writes HTML, keeps nothing in the browser and never answers a card
  ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage", "sessionStorage", "console."].forEach((b) =>
    assert.ok(!source.includes(b), b));
  assert.ok(!/\/v1\/consent/.test(source), "this file never touches the consent routes");
  assert.ok(!source.includes(String.fromCharCode(0x2014)) && !source.includes(String.fromCharCode(9)));
  assert.strictEqual(T.TITLE, "Add my own tool");

  // the list: every row by its fixed words, as text; a hostile name never in an attribute
  const P = fakePuff({ "GET /v1/tools/servers": () => ({ status: 200, data: { servers: ROWS, vault: "open" } }) });
  const shown = T.block(P);
  await flush();
  assert.strictEqual(shown.attrs["data-tile"], "tool-servers");
  const rows = findAll(shown, "data-server-row");
  assert.deepStrictEqual(rows.map((r) => r.attrs["data-server-row"]), ["my_tools", "lamps", "bad"]);
  assert.deepStrictEqual(rows.map((r) => r.attrs["data-server-state"]), ["ready", "waiting", "waiting"]);
  assert.deepStrictEqual(rows.map((r) => find(r, "data-server", "tools").text), ["2 tools", "0 tools", "1 tool"]);
  ["My tools", "127.0.0.1:8000", "this computer", "Ready.", "Refresh", "Remove", "password is in your vault"].forEach((w) => assert.ok(words(rows[0]).includes(w), w));
  assert.ok(!words(rows[1]).includes("password"));
  const hostile = [];
  walk(shown, (n) => Object.entries(n.attrs).forEach(([k, v]) => { if (k !== "text" && String(v).includes("onerror")) hostile.push(k); }));
  assert.deepStrictEqual(hostile, []);

  // the form: the password box is read once and empty before the POST answers, and a card shows inline
  let seenBox = null;
  const asked = fakePuff({
    "GET /v1/tools/servers": () => ({ status: 200, data: { servers: [] } }),
    "POST /v1/tools/servers": () => { seenBox = byTag(block2, "input")[2].value; return { status: 202, data: { state: "waiting", consent_id: "c_new", card_sha: "ab", title: "t", lines: ["l"], line: "Waiting for your Yes on the Bridge page for 192.168.1.20:8123. Nothing connects before that." } }; },
  });
  const block2 = T.block(asked);
  await flush();
  assert.ok(words(block2).includes("No tool servers added yet."));
  const [nameIn, addressIn, passwordIn] = byTag(block2, "input");
  assert.strictEqual(passwordIn.attrs.type, "password");
  const form = find(block2, "data-server", "form");
  const said = find(block2, "data-server", "form-said");
  const submit = () => form.listeners.submit({ preventDefault() {} });
  nameIn.value = "Lamps";
  addressIn.value = "python server.py";
  passwordIn.value = "pw-typed-1";
  await submit();
  assert.ok(/http/.test(said.text) && passwordIn.value === "", "a refused form still clears the password box");
  assert.strictEqual(asked.calls.filter((c) => c.method === "POST").length, 0);
  addressIn.value = " http://192.168.1.20:8123/mcp ";
  passwordIn.value = "pw-typed-2";
  await submit();
  await flush();
  const posts = asked.calls.filter((c) => c.method === "POST");
  assert.deepStrictEqual(posts.map((c) => [c.route, c.body]), [["/v1/tools/servers", { name: "Lamps", address: "http://192.168.1.20:8123/mcp", password: "pw-typed-2" }]]);
  assert.strictEqual(seenBox, "", "the box was empty while the POST was out");
  assert.deepStrictEqual(asked.inline.map((x) => x.id), ["c_new"]);
  assert.ok(said.text.includes("Bridge page") && nameIn.value === "" && addressIn.value === "");
  assert.ok(!asked.calls.some((c) => /consent|answer/.test(c.route)));

  // a refusal shows the Core's line, raises no card, and keeps what she typed but the password
  const refused = fakePuff({
    "GET /v1/tools/servers": () => ({ status: 200, data: { servers: [] } }),
    "POST /v1/tools/servers": () => ({ status: 502, data: { error: "Could not reach My tools. It may not be running. Nothing was kept." } }),
  });
  const block3 = T.block(refused);
  await flush();
  const ins = byTag(block3, "input");
  ins[0].value = "My tools"; ins[1].value = "http://127.0.0.1:9/mcp"; ins[2].value = "pw";
  await find(block3, "data-server", "form").listeners.submit({ preventDefault() {} });
  assert.strictEqual(refused.inline.length, 0);
  assert.ok(/Nothing was kept/.test(find(block3, "data-server", "form-said").text));
  assert.deepStrictEqual(ins.map((i) => i.value), ["My tools", "http://127.0.0.1:9/mcp", ""]);

  // Refresh and Remove: one call each on that row, then the list again
  let list = [ROWS[0]];
  const acting = fakePuff({
    "GET /v1/tools/servers": () => ({ status: 200, data: { servers: list } }),
    "POST /v1/tools/servers/my_tools/refresh": () => ({ status: 200, data: { line: "My tools answered. It has 2 tools your bot can use in chat." } }),
    "DELETE /v1/tools/servers/my_tools": () => { list = []; return { status: 200, data: { deleted: true, line: "My tools is removed. Its tools are gone from chat." } }; },
  });
  const block4 = T.block(acting);
  await flush();
  await find(block4, "data-server", "refresh").listeners.click();
  await flush();
  assert.ok(/answered/.test(find(block4, "data-server", "note").text));
  await find(block4, "data-server", "remove").listeners.click();
  await flush();
  assert.deepStrictEqual(acting.calls.filter((c) => c.method !== "GET").map((c) => c.method + " " + c.route),
    ["POST /v1/tools/servers/my_tools/refresh", "DELETE /v1/tools/servers/my_tools"]);
  assert.strictEqual(findAll(block4, "data-server-row").length, 0);
  assert.ok(/removed/.test(find(block4, "data-server", "note").text));

  // a refresh still waiting shows its card on that row
  const waitingP = fakePuff({
    "GET /v1/tools/servers": () => ({ status: 200, data: { servers: [ROWS[1]] } }),
    "POST /v1/tools/servers/lamps/refresh": () => ({ status: 202, data: { state: "waiting", consent_id: "c_again", line: "Waiting for your Yes." } }),
  });
  const block5 = T.block(waitingP);
  await flush();
  await find(block5, "data-server", "refresh").listeners.click();
  assert.deepStrictEqual(waitingP.inline.map((x) => x.id), ["c_again"]);

  console.log("ok");
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
