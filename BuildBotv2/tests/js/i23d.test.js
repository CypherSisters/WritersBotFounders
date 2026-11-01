// Lane i23d (D101): Toolsmith's cloud model on the Developer Overview, next to GitHub, on a small page model with
// no browser (tests/test_i23d_toolsmith_cloud_tile.py runs the same page in real Edge). Every key here is made up.
// Prints "ok".
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");

const KEY = "not-a-real-key-i23d-0000";     // made up; the checks below say it never comes back

/* ---- a small page model: only what the Desktop's code uses ---- */
class N {
  constructor(tag) { this.tag = tag; this.kids = []; this.attrs = {}; this.on = {}; this.parentNode = null; this.own = ""; this.style = {}; this._value = ""; this.checked = false; this.disabled = false; this.className = ""; this.scrollTop = 0; this.scrollHeight = 0; }
  get firstChild() { return this.kids[0] || null; }
  get lastChild() { return this.kids[this.kids.length - 1] || null; }
  get value() {
    if (this.tag !== "select") return this._value;
    return this.kids.some((o) => o._value === this._value) ? this._value : "";   // as a real select: only its options
  }
  set value(v) { this._value = String(v === undefined || v === null ? "" : v); }
  get textContent() { return this.own + this.kids.map((k) => k.textContent).join(""); }
  set textContent(v) { this.kids.forEach((k) => { k.parentNode = null; }); this.kids = []; this.own = String(v); }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.kids.push(c); return c; }
  append(...items) { items.forEach((c) => this.appendChild(typeof c === "string" ? text(c) : c)); }
  removeChild(c) { this.kids.splice(this.kids.indexOf(c), 1); c.parentNode = null; return c; }
  replaceChild(next, old) { const i = this.kids.indexOf(old); assert.ok(i >= 0); if (next.parentNode) next.parentNode.removeChild(next); this.kids[i] = next; next.parentNode = this; old.parentNode = null; return old; }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "value") this._value = String(v); if (k === "disabled") this.disabled = true; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  addEventListener(ev, fn) { (this.on[ev] = this.on[ev] || []).push(fn); }
  focus() {}
  get classList() { return { toggle: () => false }; }
  querySelectorAll(tag) { return all(this, (n) => n.tag === tag); }
  fire(ev) { (this.on[ev] || []).forEach((fn) => fn({ preventDefault() {} })); }
}
const text = (t) => { const n = new N("#text"); n.own = String(t); return n; };
const all = (root, ok) => root.kids.reduce((out, k) => out.concat(ok(k) ? [k] : [], all(k, ok)), []);
const one = (root, ok) => all(root, ok)[0] || null;
const attr = (name, v) => (n) => n.getAttribute(name) === v;
const flush = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
const button = (root, words) => one(root, (n) => n.tag === "button" && n.textContent.indexOf(words) >= 0);
/* Everything the page holds: its words, every attribute and every box's value. */
const everything = (root) => [root.own, root._value, JSON.stringify(root.attrs)].concat(root.kids.map(everything)).join(" ");

/* ---- the Desktop's own parts, as desktop.js makes them ---- */
global.document = { createElement: (t) => new N(t), createTextNode: text, getElementById: () => null };
const spied = [];
const store = { getItem: () => null, setItem: (k, v) => spied.push(String(k) + String(v)), removeItem() {} };
global.window = global;
Object.defineProperty(global, "localStorage", { value: store, configurable: true });
Object.defineProperty(global, "sessionStorage", { value: store, configurable: true });
global.location = { search: "", hash: "#developer", href: "http://puffpai.localhost/#developer" };
const opened = [];
global.open = (...a) => { opened.push(a); return null; };
["log", "info", "warn", "error", "debug"].forEach((m) => { const was = console[m]; console[m] = (...a) => { spied.push(a.join(" ")); if (m === "log" && a[0] === "ok") was(...a); }; });

function el(tag, attrs, kids) {
  const n = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "text") n.textContent = v;
    else if (k === "on") Object.entries(v).forEach(([ev, fn]) => n.addEventListener(ev, fn));
    else if (k === "class") n.className = v;
    else n.setAttribute(k, v === true ? "" : v);
  });
  if (tag === "button" && !n.hasAttribute("type")) n.setAttribute("type", "button");
  (kids || []).forEach((c) => c && n.appendChild(typeof c === "string" ? text(c) : c));
  return n;
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const icon = (name) => el("span", { class: "msr", text: name });

const L = require(path.join(web, "logic.js"));
global.PuffDev = require(path.join(web, "devzones.js"));
const D = global.PuffDev;
let core = null;
let draw = null;
global.Puff = {
  L, el, clear, icon,
  api: async (method, p, body) => core.api(method, p, body),
  said: (r, fb) => (r && r.data && (r.data.error || r.data.line)) || fb || "That did not work.",
  missing: (r) => r.status === 501 || r.status === 405,
  later: (m) => el("div", { class: "card later", text: m }),
  names: () => ({}),
  page: (name, def) => { draw = def.draw; },
  tile(o) {
    const body = el("div", { class: "tile-body" });
    const head = el("div", { class: "tile-head" }, [icon(o.icon), el("h2", { text: o.title || "" }), o.stat ? el("span", { class: "stat", text: o.stat }) : null]);
    return { node: el("section", { class: "tile", "data-tile": o.id }, [head, body]), body, head };
  },
  kv(pairs) {
    const dl = el("dl", { class: "kv" });
    pairs.forEach(([k, v]) => { if (v === undefined || v === null || v === "") return; dl.appendChild(el("dt", { text: k })); dl.appendChild(el("dd", { text: String(v) })); });
    return dl;
  },
  pin: (label) => el("input", { type: "password", placeholder: label }),
  drawPrivate() {},
  bridgeButton: async () => null,
  drawChips() {},
  signinWhere: async () => null,
  consentInline() {},
  loadRegistry: async () => [],
  sleep: async () => {},
};
global.PuffGitHub = (() => { const m = { exports: {} };
  vm.runInThisContext("(function (module) {" + fs.readFileSync(path.join(web, "github.js"), "utf-8") + "\n})")(m); return m.exports; })();
vm.runInThisContext(fs.readFileSync(path.join(web, "pages", "developer.js"), "utf-8"));

/* ---- a Core with the same answers as the routes (the routes themselves: the pytest file) ---- */
const PROVIDERS = [
  { id: "anthropic", company: "Anthropic", models: [{ id: "a-big", name: "A Big" }, { id: "a-small", name: "A Small" }], default_model: "a-big", cost_cap_usd: 5,
    consent: "What Toolsmith sends to Anthropic: the job name.", consent_sha: "sha-a", problems: [], key: { vault_name: "toolsmith.anthropic", get_it: "https://example.invalid/a" } },
  { id: "openai", company: "OpenAI", models: [{ id: "o-big", name: "O Big" }], default_model: "o-big", cost_cap_usd: 3,
    consent: "What Toolsmith sends to OpenAI: the job name.", consent_sha: "sha-o", problems: [], key: { vault_name: "toolsmith.openai", get_it: "https://example.invalid/o" } },
  { id: "google", company: "Google", models: [{ id: "g-big", name: "G Big" }], default_model: "g-big", cost_cap_usd: 4,
    consent: "What Toolsmith sends to Google: the job name.", consent_sha: "sha-g", problems: [], key: { vault_name: "toolsmith.google", get_it: "javascript:alert(1)" } },
];
function fakeCore(o) {
  const c = Object.assign({ where: "local", provider: "", model: "", cap: 0, vault: "open", kept: {}, calls: [], keyRefuse: null, modelRefuse: null, keysDown: false }, o);
  const keyList = () => PROVIDERS.map((p) => ({ provider: p.id, company: p.company, vault_name: p.key.vault_name, kept: c.vault === "open" ? !!c.kept[p.id] : null }));
  c.api = async (method, p, body) => {
    const call = method + " " + p;
    if (method !== "GET") c.calls.push({ call, body });
    const co = (id) => PROVIDERS.find((x) => x.id === id);
    switch (call) {
      case "GET /v1/vault/status": return { status: 200, data: { state: c.vault, inbox: 0, items: 0 } };
      case "GET /v1/model": return { status: 200, data: { settings: { model: "m" }, state: { state: "ready" } } };
      case "GET /v1/bridge": return { status: 200, data: { state: "closed", setting: "closed", search: false, switches: { "toolsmith:search": !!c.searchOn } } };
      case "POST /v1/bridge/search": c.searchOn = false; return { status: 200, data: {} };
      case "GET /v1/bridge/ledger": case "GET /v1/bridge/ledger?limit=20": return { status: 200, data: [] };
      case "GET /v1/toolsmith": return { status: 200, data: c.where === "cloud" ? { where: "cloud", provider: c.provider, company: co(c.provider).company, model: c.model, cost_cap_usd: c.cap, ...(c.changed ? { consent_changed: c.changed } : {}), builds: [] } : { where: "local", builds: [] } };
      case "GET /v1/toolsmith/providers": return { status: 200, data: PROVIDERS };
      case "GET /v1/toolsmith/keys": return c.keysDown ? { status: 500, data: { error: "down" } } : { status: 200, data: keyList() };
      case "GET /v1/toolsmith/hosts": return { status: 200, data: { search: { hosts: [], on: false }, cloud: { on: false }, knowledge: { hosts: [] }, line: "" } };
      case "GET /v1/settings/drawbridge": return { status: 200, data: { files: "normal", title: "T", choices: [] } };
      case "GET /v1/github/key": return { status: 200, data: { linked: false, line: "Not linked yet." } };
      case "PUT /v1/github/key": return { status: 200, data: { line: "Linked." } };
      case "GET /v1/dev/shell": return { status: 200, data: [] };
      case "PUT /v1/toolsmith/cap": c.cap = body.cost_cap_usd; return { status: 200, data: { cost_cap_usd: c.cap } };
      case "PUT /v1/toolsmith/model": {
        if (c.modelRefuse) return { status: 400, data: { error: c.modelRefuse } };
        if (body.where !== "cloud") { c.where = "local"; return { status: 200, data: { where: "local" } }; }
        if (body.consent_sha !== co(body.provider).consent_sha) return { status: 400, data: { error: "Read what Toolsmith sends and say yes to that sentence first." } };
        Object.assign(c, { where: "cloud", provider: body.provider, model: body.model, cap: body.cost_cap_usd, changed: "" });
        return { status: 200, data: { where: "cloud", provider: body.provider, model: body.model, cost_cap_usd: body.cost_cap_usd } };
      }
    }
    if (p.startsWith("GET /v1/audit") || (method === "GET" && p.startsWith("/v1/audit"))) return { status: 200, data: { chain_ok: true, lines: [] } };
    if (method === "GET" && p === "/v1/providers") return { status: 200, data: [] };
    if (method === "GET" && p === "/v1/signin") return { status: 200, data: {} };   // lane pages: the parts tile, on Overview
    const m = /^\/v1\/toolsmith\/keys\/(.+)$/.exec(p);
    if (m && method === "PUT") {
      if (c.keyRefuse) return { status: c.keyRefuse.status, data: { error: c.keyRefuse.error } };
      if (c.vault === "not_set_up") return { status: 409, data: { error: "Set up the vault first, so your key has a safe home." } };
      if (c.vault === "locked") return { status: 202, data: { saved: false, line: "Sealed in your vault's inbox. It is filed the next time you unlock." } };
      c.kept[m[1]] = true;
      return { status: 200, data: { saved: true, line: "Saved in your vault." } };
    }
    if (m && method === "DELETE") { delete c.kept[m[1]]; return { status: 200, data: { line: "Your key is out of your vault." } }; }
    throw new Error("the page asked for a route this test does not know: " + call);
  };
  return c;
}

const noKeyAnywhere = (sec, c) => {
  assert.ok(everything(sec).indexOf(KEY) < 0, "the key is on the page");
  assert.ok(spied.join(" ").indexOf(KEY) < 0, "the key went to storage or the console");
  assert.ok(location.href.indexOf(KEY) < 0 && location.hash.indexOf(KEY) < 0);
  c.calls.forEach((x) => { assert.ok(x.call.indexOf(KEY) < 0, "the key is in an address: " + x.call); });
  c.calls.filter((x) => JSON.stringify(x.body || {}).indexOf(KEY) >= 0).forEach((x) => assert.ok(/^PUT \/v1\/toolsmith\/keys\//.test(x.call), x.call));
};
const tileOf = (sec) => one(sec, attr("data-tile", "toolsmith"));
const pickOf = (t) => one(t, attr("aria-label", "Where Toolsmith thinks"));
const statOf = (t) => one(t, attr("data-toolsmith", "stat")).textContent;
const nowOf = (t) => one(t, attr("data-toolsmith", "now")).textContent;
const saidOf = (t, name) => one(t, attr("data-said", name)).textContent;
const keyBoxOf = (t) => one(t, (n) => n.tag === "input" && n.getAttribute("type") === "password");
const choose = async (t, id) => { const s = pickOf(t); s.value = id; s.fire("change"); await flush(); };
const tick = (t) => { one(t, attr("aria-label", "Yes to what Toolsmith sends")).checked = true; };
async function page(c, arg) { core = c; const sec = new N("section"); await draw(sec, arg, false); await flush(); return sec; }
/* Holds each call named *match* ("PUT /v1/...") until its turn: c.let() lets the oldest go on, or answers with *over*. */
function hold(c, match) {
  const real = c.api;
  const waiting = [];
  c.api = (m, p, b) => {
    if (m + " " + p !== match) return real(m, p, b);
    return new Promise((res, rej) => waiting.push((over) => {
      if (over instanceof Error) { rej(over); return; }
      if (over) { if (m !== "GET") c.calls.push({ call: m + " " + p, body: b }); res(over); return; }
      res(real(m, p, b));
    }));
  };
  c.waiting = waiting;
  c.let = async (over) => { assert.ok(waiting.length, "nothing is being held"); waiting.shift()(over); await flush(); };
  c.release = () => { c.api = real; };
  return c;
}
const controls = (t) => ({
  pick: pickOf(t),
  save: button(t, "Save"),
  box: keyBoxOf(t),
  tick: one(t, attr("aria-label", "Yes to what Toolsmith sends")),
  model: one(t, attr("aria-label", "Model")),
  cap: one(t, attr("aria-label", "Cost cap per build, dollars")),
  keep: one(t, attr("data-toolsmith", "key-save")),
  out: button(t, "Take the key out"),
});
const allHeld = (t, on) => Object.entries(controls(t)).forEach(([name, n]) => { if (n) assert.strictEqual(n.disabled, on, name + " disabled should be " + on); });
const KEY2 = "not-a-real-key-i23d-1111";

(async () => {
  /* ---- the logic: names only, and can't tell is never "kept" or "no key" ---- */
  const cloud = { where: "cloud", provider: "openai", company: "OpenAI", model: "o-big", cost_cap_usd: 3 };
  assert.strictEqual(D.toolsmithNow(cloud, [{ provider: "openai", kept: true }], "open").stat, "KEY KEPT");
  assert.strictEqual(D.toolsmithNow(cloud, [{ provider: "openai", kept: false }], "open").stat, "NO KEY");
  assert.strictEqual(D.toolsmithNow(cloud, [{ provider: "openai", kept: null }], "locked").stat, "VAULT LOCKED");
  assert.strictEqual(D.toolsmithNow(cloud, [{ provider: "openai", kept: null }], "not_set_up").stat, "NO KEY");
  assert.strictEqual(D.toolsmithNow(cloud, [], "open").stat, "CAN'T TELL");
  assert.ok(/Can't tell/.test(D.toolsmithNow(cloud, [], "open").key));
  assert.strictEqual(D.toolsmithNow({ where: "local" }, [{ provider: "a", kept: false }], "open").stat, "NO KEY");
  assert.ok(/A key is kept for Anthropic/.test(D.toolsmithNow({ where: "local" }, [{ provider: "a", company: "Anthropic", kept: true }, { provider: "b", kept: false }], "open").key));
  assert.strictEqual(D.toolsmithNow({ where: "local" }, [{ provider: "a", kept: null }], "locked").stat, "VAULT LOCKED");
  assert.strictEqual(D.toolsmithNow({ where: "local" }, [], "open").stat, "CAN'T TELL");

  /* ---- a local bot: the tile is next to GitHub, says This computer, and "Pick a company first" ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const own = one(sec, attr("data-dev", "add-your-own"));
    assert.deepStrictEqual(own.kids.map((n) => n.getAttribute("data-tile")), ["own-folder", "github", "toolsmith"]);
    const t = tileOf(sec);
    assert.strictEqual(one(t, (n) => n.tag === "h2").textContent, "Toolsmith's cloud model");
    assert.ok(/This computer/.test(nowOf(t)) && /No key is kept/.test(nowOf(t)), nowOf(t));
    assert.strictEqual(statOf(t), "NO KEY");
    assert.strictEqual(pickOf(t).value, "");
    assert.ok(/^Pick a company first/.test(one(t, attr("data-toolsmith", "pick-first")).textContent));
    assert.strictEqual(keyBoxOf(t).disabled, true);
    assert.strictEqual(keyBoxOf(t).getAttribute("placeholder"), "Pick a company first");
    assert.strictEqual(one(t, attr("data-toolsmith", "consent")), null);
    assert.deepStrictEqual(c.calls, []);                       // nothing is written by looking
  }

  /* ---- three companies: at a glance, the box straight away, and "Change the key" once one is kept ---- */
  for (const p of PROVIDERS) {
    const kept = fakeCore({ where: "cloud", provider: p.id, model: p.default_model, cap: p.cost_cap_usd, kept: { [p.id]: true } });
    let t = tileOf(await page(kept, ""));
    assert.ok(nowOf(t).indexOf(p.company + ", " + p.default_model) >= 0, nowOf(t));
    assert.ok(/Its key is kept in your vault/.test(nowOf(t)), nowOf(t));
    assert.strictEqual(statOf(t), "KEY KEPT");
    assert.strictEqual(pickOf(t).value, p.id);
    assert.strictEqual(keyBoxOf(t).disabled, false);
    assert.strictEqual(keyBoxOf(t).getAttribute("type"), "password");
    assert.strictEqual(one(t, attr("data-toolsmith", "consent")).textContent, p.consent);   // her sentence, unchanged
    assert.ok(button(t, "Change the key") && button(t, "Take the key out"));
    assert.strictEqual(button(t, "Save the key"), null);
    assert.strictEqual(one(t, attr("data-toolsmith", "pick-first")), null);

    const none = fakeCore({ where: "cloud", provider: p.id, model: p.default_model, cap: p.cost_cap_usd });
    t = tileOf(await page(none, ""));
    assert.strictEqual(statOf(t), "NO KEY");
    assert.ok(/No key is kept yet/.test(nowOf(t)), nowOf(t));
    assert.ok(button(t, "Save the key") && !button(t, "Take the key out"));

    /* a local bot picks this company: its sentence and box; Save sends the key, then the model with the sha */
    const c = fakeCore();
    const sec = await page(c, "");
    t = tileOf(sec);
    const shell = one(sec, attr("data-tile", "terminal"));
    await choose(t, p.id);
    assert.strictEqual(one(t, attr("data-toolsmith", "consent")).textContent, p.consent);
    assert.ok(keyBoxOf(t).disabled === false && keyBoxOf(t).getAttribute("aria-label") === p.company + " key");
    keyBoxOf(t).value = KEY;
    button(t, "Save").fire("click");                           // no tick yet: nothing is sent, nothing changes
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.ok(/Tick Yes to what Toolsmith sends first/.test(saidOf(t, "toolsmith")));
    tick(t);
    const box = keyBoxOf(t);
    button(t, "Save").fire("click");
    assert.strictEqual(box.value, "");                         // cleared the moment it is read
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/" + p.id, "PUT /v1/toolsmith/model"]);
    assert.deepStrictEqual(c.calls[0].body, { value: KEY });
    assert.deepStrictEqual(c.calls[1].body, { where: "cloud", provider: p.id, model: p.default_model, cost_cap_usd: p.cost_cap_usd, consent_sha: p.consent_sha });
    noKeyAnywhere(sec, c);
    t = tileOf(sec);                                           // drawn again in its place, the Now up to date
    assert.ok(nowOf(t).indexOf(p.company + ", " + p.default_model) >= 0 && statOf(t) === "KEY KEPT", nowOf(t));
    assert.ok(/Saved in your vault\. Saved: /.test(saidOf(t, "toolsmith")), saidOf(t, "toolsmith"));
    assert.strictEqual(keyBoxOf(t).value, "");
    assert.ok(button(t, "Change the key"));
    assert.strictEqual(one(sec, attr("data-tile", "terminal")), shell);   // a running command line keeps its screen
    assert.strictEqual(one(sec, attr("data-dev", "add-your-own")).kids[2], t);
  }

  /* ---- D118: a bot with a saved Yes for an earlier sentence is told why, and Save needs her tick ---- */
  for (const p of PROVIDERS) {
    const why = "What Toolsmith sends to " + p.company + " now reads differently. It now also sends other text files from each app's own download, like its source code.";
    const c = fakeCore({ where: "cloud", provider: p.id, model: p.default_model, cap: p.cost_cap_usd, kept: { [p.id]: true }, changed: why });
    const sec = await page(c, "");
    let t = tileOf(sec);
    const said = one(t, attr("data-toolsmith", "consent-changed")).textContent;
    assert.ok(said.indexOf(why) === 0 && /nothing until you tick Yes and press Save/.test(said), said);
    assert.strictEqual(one(t, attr("data-toolsmith", "consent")).textContent, p.consent);
    button(t, "Save").fire("click");                           // no tick, the same model and cap: it never reads as saved
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.ok(/Tick Yes to what Toolsmith sends first/.test(saidOf(t, "toolsmith")), saidOf(t, "toolsmith"));
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/model"]);
    assert.strictEqual(c.calls[0].body.consent_sha, p.consent_sha);
    t = tileOf(sec);
    assert.strictEqual(one(t, attr("data-toolsmith", "consent-changed")), null);   // her Yes ended it
    /* another company on that bot, a first Yes for it: no such line */
    const other = PROVIDERS.find((x) => x.id !== p.id);
    const c2 = fakeCore({ where: "cloud", provider: p.id, model: p.default_model, cap: p.cost_cap_usd, changed: why });
    const t2 = tileOf(await page(c2, ""));
    await choose(t2, other.id);
    assert.strictEqual(one(t2, attr("data-toolsmith", "consent-changed")), null);
    /* a saved Yes for this very sentence, and a new bot: no such line */
    const fresh = tileOf(await page(fakeCore({ where: "cloud", provider: p.id, model: p.default_model, cap: p.cost_cap_usd }), ""));
    assert.strictEqual(one(fresh, attr("data-toolsmith", "consent-changed")), null);
    const first = fakeCore();
    const t3 = tileOf(await page(first, ""));
    await choose(t3, p.id);
    assert.strictEqual(one(t3, attr("data-toolsmith", "consent-changed")), null);
  }

  /* ---- "Save the key" alone: her tick and choices stay; the corner and the Now change ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "openai");
    tick(t);
    keyBoxOf(t).value = KEY;
    button(t, "Save the key").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/openai"]);
    assert.strictEqual(c.where, "local");                      // a key alone never moves Toolsmith to a company
    noKeyAnywhere(sec, c);
    assert.strictEqual(statOf(t), "KEY KEPT");
    assert.ok(/A key is kept for OpenAI/.test(nowOf(t)), nowOf(t));
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Saved in your vault.");
    assert.strictEqual(one(t, attr("aria-label", "Yes to what Toolsmith sends")).checked, true);
    assert.strictEqual(keyBoxOf(t).value, "");
    button(t, "Take the key out").fire("click");
    await flush();
    assert.strictEqual(statOf(t), "NO KEY");
    keyBoxOf(t).value = "   ";
    button(t, "Save the key").fire("click");
    await flush();
    assert.strictEqual(saidOf(t, "toolsmith-key"), "That key is empty.");
    assert.strictEqual(keyBoxOf(t).value, "");
    c.calls.length = 0;
    button(t, "Save the key").fire("click");
    await flush();
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Paste the key first.");
    assert.deepStrictEqual(c.calls, []);
  }

  /* ---- a key pasted for one company never goes to the next one picked ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    await choose(t, "google");
    assert.strictEqual(keyBoxOf(t).value, "");
    assert.strictEqual(one(t, attr("data-toolsmith", "consent")).textContent, PROVIDERS[2].consent);
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/model"]);
    await choose(tileOf(sec), "");
    assert.ok(/^Pick a company first/.test(one(tileOf(sec), attr("data-toolsmith", "pick-first")).textContent));
    noKeyAnywhere(sec, c);
  }

  /* ---- refused: a key that does not go in changes nothing; a refused sentence says so, and the key's state is true ---- */
  {
    const c = fakeCore({ keyRefuse: { status: 400, error: "Paste the key OpenAI gave you, on one line." } });
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "openai");
    tick(t);
    keyBoxOf(t).value = KEY;
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/openai"]);
    assert.strictEqual(c.where, "local");
    assert.strictEqual(saidOf(t, "toolsmith"), "Paste the key OpenAI gave you, on one line.");
    assert.strictEqual(keyBoxOf(t).value, "");
    noKeyAnywhere(sec, c);

    const d = fakeCore({ modelRefuse: "Read what Toolsmith sends to Google and say yes to that sentence first." });
    const sec2 = await page(d, "");
    const t2 = tileOf(sec2);
    await choose(t2, "google");
    tick(t2);
    keyBoxOf(t2).value = KEY;
    button(t2, "Save").fire("click");
    await flush();
    assert.strictEqual(d.where, "local");
    assert.ok(/say yes to that sentence/.test(saidOf(t2, "toolsmith")));
    assert.strictEqual(saidOf(t2, "toolsmith-key"), "Saved in your vault.");
    assert.strictEqual(statOf(t2), "KEY KEPT");
    noKeyAnywhere(sec2, d);
  }

  /* ---- the same company, a lower cap, with a new key and no new Yes: the key, then the lower cap at once ---- */
  {
    const c = fakeCore({ where: "cloud", provider: "anthropic", model: "a-big", cap: 5, kept: { anthropic: true } });
    const sec = await page(c, "");
    const t = tileOf(sec);
    keyBoxOf(t).value = KEY;
    one(t, attr("aria-label", "Cost cap per build, dollars")).value = "2";
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/anthropic", "PUT /v1/toolsmith/cap"]);
    assert.ok(/at most \$2 per build/.test(saidOf(tileOf(sec), "toolsmith")));
    noKeyAnywhere(sec, c);
    /* a higher cap still needs her Yes, and nothing is sent without it */
    const u = tileOf(sec);
    keyBoxOf(u).value = KEY;
    one(u, attr("aria-label", "Cost cap per build, dollars")).value = "50";
    c.calls.length = 0;
    button(u, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.ok(/A higher cap needs your Yes again/.test(saidOf(u, "toolsmith")));
    /* back to this computer: at once, with no key call */
    await choose(u, "");
    button(u, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/model"]);
    assert.deepStrictEqual(c.calls[0].body, { where: "local" });
    assert.ok(/This computer/.test(nowOf(tileOf(sec))));
  }

  /* ---- a locked vault: can't tell, never "kept" or "no key"; a key goes to the inbox, as the route says ---- */
  {
    const c = fakeCore({ where: "cloud", provider: "google", model: "g-big", cap: 4, vault: "locked" });
    const sec = await page(c, "");
    const t = tileOf(sec);
    assert.strictEqual(statOf(t), "VAULT LOCKED");
    assert.ok(/Open your vault to see whether its key is kept/.test(nowOf(t)), nowOf(t));
    assert.ok(/Your vault is locked/.test(one(t, attr("data-toolsmith", "key")).textContent));
    assert.strictEqual(button(t, "Take the key out"), null);
    keyBoxOf(t).value = KEY;
    button(t, "Save the key").fire("click");
    await flush();
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Sealed in your vault's inbox. It is filed the next time you unlock.");
    assert.strictEqual(statOf(t), "VAULT LOCKED");
    assert.strictEqual(keyBoxOf(t).value, "");
    noKeyAnywhere(sec, c);

    const l = tileOf(await page(fakeCore({ vault: "locked" }), ""));
    assert.strictEqual(statOf(l), "VAULT LOCKED");
    assert.ok(/This computer/.test(nowOf(l)) && /Open your vault/.test(nowOf(l)), nowOf(l));
  }

  /* ---- no vault yet: says so, links to it, and the route's own words come back ---- */
  {
    const c = fakeCore({ vault: "not_set_up" });
    const sec = await page(c, "");
    const t = tileOf(sec);
    assert.strictEqual(statOf(t), "NO KEY");
    assert.ok(/Set up the vault first/.test(nowOf(t)));
    await choose(t, "openai");
    assert.ok(one(t, (n) => n.tag === "a" && n.getAttribute("href") === "#developer/vault"));
    keyBoxOf(t).value = KEY;
    button(t, "Save the key").fire("click");
    await flush();
    assert.ok(/Set up the vault first/.test(saidOf(t, "toolsmith-key")));
    assert.strictEqual(keyBoxOf(t).value, "");
    noKeyAnywhere(sec, c);
  }

  /* ---- the keys route down: "can't tell", never "no key" ---- */
  {
    const t = tileOf(await page(fakeCore({ where: "cloud", provider: "openai", model: "o-big", cap: 3, keysDown: true }), ""));
    assert.strictEqual(statOf(t), "CAN'T TELL");
    assert.ok(/Can't tell/.test(nowOf(t)) && /Can't tell/.test(one(t, attr("data-toolsmith", "key")).textContent));
  }

  /* ---- "Get a key" opens an https address only, in a new tab with no way back to this page ---- */
  {
    const t = tileOf(await page(fakeCore(), ""));
    await choose(t, "openai");
    button(t, "Get a key").fire("click");
    assert.deepStrictEqual(opened.pop(), ["https://example.invalid/o", "_blank", "noopener,noreferrer"]);
    await choose(t, "google");
    assert.strictEqual(button(t, "Get a key"), null);          // this made-up provider's address is not https
  }

  /* ---- the GitHub block is still there, with its own form and its own route ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const g = one(sec, attr("data-tile", "github"));
    assert.strictEqual(g.getAttribute("data-github"), "developer");
    assert.strictEqual(one(g, (n) => n.tag === "h2").textContent, "GitHub");
    assert.deepStrictEqual(all(g, (n) => n.tag === "h3").map((n) => n.textContent), ["Link your GitHub", "Add code from GitHub"]);
    const field = one(g, attr("aria-label", "GitHub token"));
    assert.strictEqual(field.getAttribute("type"), "password");
    const token = "a".repeat(30);
    field.value = token;
    one(g, (n) => n.tag === "form").fire("submit");
    await flush();
    assert.deepStrictEqual(c.calls, [{ call: "PUT /v1/github/key", body: { value: token } }]);
  }

  /* ---- Bridge: the tile and a card to its page, next to GitHub's; its Saved line goes where it did ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "bridge");
    const t = tileOf(sec);
    assert.ok(t && one(t, (n) => n.tag === "h2").textContent === "Toolsmith's cloud model");
    assert.deepStrictEqual(one(sec, attr("data-dev", "goto")).kids.map((n) => n.getAttribute("data-goto")), ["#outside", "#developer/github", "#developer/toolsmith"]);
    await choose(t, "anthropic");
    tick(t);
    keyBoxOf(t).value = KEY;
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/anthropic", "PUT /v1/toolsmith/model"]);
    assert.ok(/Saved: Anthropic, a-big/.test(one(sec, attr("data-said", "bridge")).textContent));
    assert.ok(/Anthropic, a-big/.test(nowOf(tileOf(sec))));
    noKeyAnywhere(sec, c);
  }

  /* ---- its own page, like GitHub's ---- */
  {
    const c = fakeCore({ where: "cloud", provider: "openai", model: "o-big", cap: 3, kept: { openai: true } });
    const sec = await page(c, "toolsmith");
    const t = tileOf(sec);
    assert.ok(/OpenAI, o-big/.test(nowOf(t)) && statOf(t) === "KEY KEPT");
    assert.ok(/Nothing leaves your computer without your consent\./.test(sec.textContent));
    assert.strictEqual(one(sec, attr("data-dev", "back")).getAttribute("href"), "#developer");
    const t0 = t;
    await choose(t, "");
    button(t, "Save").fire("click");
    await flush();
    assert.notStrictEqual(tileOf(sec), t0);
    assert.strictEqual(saidOf(tileOf(sec), "toolsmith"), "Saved: Toolsmith uses this computer.");
  }

  /* ---- both drawn at once: each keeps its own box, its own line and its own company ---- */
  {
    const c = fakeCore();
    const over = await page(c, "");
    const bridge = await page(c, "bridge");
    const a = tileOf(over), b = tileOf(bridge);
    await choose(a, "anthropic");
    assert.strictEqual(pickOf(b).value, "");
    assert.ok(one(b, attr("data-toolsmith", "pick-first")));
    await choose(b, "google");
    keyBoxOf(a).value = KEY;
    assert.strictEqual(keyBoxOf(b).value, "");
    button(a, "Save the key").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/anthropic"]);
    assert.strictEqual(saidOf(a, "toolsmith-key"), "Saved in your vault.");
    assert.strictEqual(saidOf(b, "toolsmith-key"), "");
    noKeyAnywhere(over, c); noKeyAnywhere(bridge, c);
  }

  /* ---- one thing at a time: a key saved alone, held. Defect 1: the next company's key is never dropped ---- */
  {
    const c = hold(fakeCore(), "PUT /v1/toolsmith/keys/anthropic");
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    button(t, "Save the key").fire("click");
    await flush();
    assert.strictEqual(keyBoxOf(t).value, "");
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Saving...");
    allHeld(t, true);
    await choose(t, "openai");                                 // a list that is held does not move, even if it is told to
    assert.strictEqual(pickOf(t).value, "anthropic");
    assert.strictEqual(keyBoxOf(t).getAttribute("aria-label"), "Anthropic key");
    await c.let();
    allHeld(t, false);
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Saved in your vault.");
    assert.strictEqual(statOf(t), "KEY KEPT");
    c.release();
    c.calls.length = 0;
    await choose(t, "openai");
    assert.strictEqual(keyBoxOf(t).getAttribute("aria-label"), "OpenAI key");
    keyBoxOf(t).value = KEY2;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/openai", "PUT /v1/toolsmith/model"]);
    assert.deepStrictEqual(c.calls[0].body, { value: KEY2 });
    assert.strictEqual(c.provider, "openai");
    assert.strictEqual(c.kept.openai, true);
  }

  /* ---- the same, down the path where the model is refused after the key went in ---- */
  {
    const c = hold(fakeCore({ modelRefuse: "Read what Toolsmith sends to Anthropic and say yes to that sentence first." }), "PUT /v1/toolsmith/keys/anthropic");
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.strictEqual(saidOf(t, "toolsmith"), "Saving...");
    allHeld(t, true);
    await choose(t, "openai");
    assert.strictEqual(pickOf(t).value, "anthropic");
    await c.let();
    allHeld(t, false);
    assert.ok(/say yes to that sentence/.test(saidOf(t, "toolsmith")));
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Saved in your vault.");
    c.release();
    c.modelRefuse = null;
    c.calls.length = 0;
    await choose(t, "openai");
    keyBoxOf(t).value = KEY2;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/openai", "PUT /v1/toolsmith/model"]);
    assert.deepStrictEqual(c.calls[0].body, { value: KEY2 });
  }

  /* ---- defect 2: Save pressed while a key is on its way does nothing, and a refusal stays on screen ---- */
  {
    const c = hold(fakeCore(), "PUT /v1/toolsmith/keys/anthropic");
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    button(t, "Save the key").fire("click");
    await flush();
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    await c.let({ status: 400, data: { error: "Paste the key Anthropic gave you, on one line." } });
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/anthropic"]);
    assert.strictEqual(c.where, "local");
    assert.strictEqual(tileOf(sec), t);                        // not drawn again
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Paste the key Anthropic gave you, on one line.");
    assert.ok(saidOf(t, "toolsmith").indexOf("Saved") < 0);
    assert.strictEqual(statOf(t), "NO KEY");
    allHeld(t, false);
    /* Save's own key, refused while held: no model, the refusal stays, she can try again */
    const d = hold(fakeCore(), "PUT /v1/toolsmith/keys/openai");
    const sec2 = await page(d, "");
    const u = tileOf(sec2);
    await choose(u, "openai");
    keyBoxOf(u).value = KEY;
    tick(u);
    button(u, "Save").fire("click");
    await flush();
    allHeld(u, true);
    await d.let({ status: 400, data: { error: "Paste the key OpenAI gave you, on one line." } });
    assert.deepStrictEqual(d.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/openai"]);
    assert.strictEqual(saidOf(u, "toolsmith"), "Paste the key OpenAI gave you, on one line.");
    assert.strictEqual(d.where, "local");
    allHeld(u, false);
    assert.strictEqual(one(u, attr("aria-label", "Yes to what Toolsmith sends")).checked, true);
    noKeyAnywhere(sec2, d);
  }

  /* ---- defect 3: during a slow Save, and its redraw, the box takes nothing, so nothing pasted can be lost ---- */
  {
    const c = hold(fakeCore(), "PUT /v1/toolsmith/model");
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["PUT /v1/toolsmith/keys/anthropic"]);
    assert.strictEqual(saidOf(t, "toolsmith"), "Saving...");
    allHeld(t, true);                                          // the key went; the model is held
    const real = c.api;
    let waitRead = null;
    c.api = (m, p, b) => {                                     // and now the read for the redraw is held too
      if (m === "GET" && p === "/v1/toolsmith") return new Promise((res) => { waitRead = () => res(real(m, p, b)); });
      return real(m, p, b);
    };
    c.waiting.shift()();
    await flush();
    assert.ok(waitRead, "the redraw has not started");
    assert.strictEqual(tileOf(sec), t);
    allHeld(t, true);                                          // still closed while it draws again
    waitRead();
    await flush();
    const u = tileOf(sec);
    assert.notStrictEqual(u, t);
    allHeld(u, false);
    assert.ok(/Saved: Anthropic/.test(saidOf(u, "toolsmith")));
    noKeyAnywhere(sec, c);
  }

  /* ---- a redraw of another tile on Bridge leaves a key she pasted, unsent, in her box ---- */
  {
    const c = fakeCore({ searchOn: true });
    const sec = await page(c, "bridge");
    const t = tileOf(sec);
    await choose(t, "google");
    keyBoxOf(t).value = KEY;
    tick(t);
    button(sec, "Turn off Toolsmith's search").fire("click");
    await flush();
    assert.strictEqual(c.searchOn, false);
    assert.ok(button(sec, "Let Toolsmith search while it builds"));   // the Bridge tile did draw again
    assert.strictEqual(tileOf(sec), t);
    assert.strictEqual(keyBoxOf(t).value, KEY);
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["POST /v1/bridge/search"]);
    button(t, "Save").fire("click");                           // and it still goes, to the right place
    await flush();
    assert.deepStrictEqual(c.calls.map((x) => x.call), ["POST /v1/bridge/search", "PUT /v1/toolsmith/keys/google", "PUT /v1/toolsmith/model"]);
    assert.ok(/Saved: Google/.test(one(sec, attr("data-said", "bridge")).textContent));
    assert.strictEqual(keyBoxOf(tileOf(sec)).value, "");
    noKeyAnywhere(sec, c);
  }

  /* ---- everything opens again after a refusal and after a thrown error, and an error's own words never show ---- */
  for (const route of ["PUT /v1/toolsmith/keys/openai", "PUT /v1/toolsmith/model"]) {
    const c = hold(fakeCore(), route);
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "openai");
    keyBoxOf(t).value = KEY;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    allHeld(t, true);
    await c.let(new Error("boom " + KEY));
    allHeld(t, false);
    assert.strictEqual(saidOf(t, "toolsmith"), "That did not finish. Nothing more was sent.");
    assert.strictEqual(c.where, "local");
    noKeyAnywhere(sec, c);
    /* and she can go again */
    keyBoxOf(t).value = KEY2;
    button(t, "Save").fire("click");
    await flush();
    await c.let({ status: 400, data: { error: "Not this time." } });
    allHeld(t, false);
    assert.strictEqual(saidOf(t, "toolsmith"), "Not this time.");
    c.release();
    keyBoxOf(t).value = KEY2;
    button(t, "Save").fire("click");
    await flush();
    assert.strictEqual(c.provider, "openai");
    assert.ok(/Saved: OpenAI/.test(saidOf(tileOf(sec), "toolsmith")));
  }
  /* "Save the key" and "Take the key out", thrown; "This computer", held */
  {
    const c = hold(fakeCore({ where: "cloud", provider: "openai", model: "o-big", cap: 3, kept: { openai: true } }), "PUT /v1/toolsmith/keys/openai");
    const t = tileOf(await page(c, ""));
    keyBoxOf(t).value = KEY;
    button(t, "Change the key").fire("click");
    await flush();
    assert.strictEqual(saidOf(t, "toolsmith-key"), "Saving...");
    allHeld(t, true);
    button(t, "Take the key out").fire("click");               // told to anyway: no second write, no other line
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.strictEqual(saidOf(t, "toolsmith"), "");
    await c.let(new Error("boom"));
    allHeld(t, false);
    assert.strictEqual(saidOf(t, "toolsmith-key"), "That did not finish. Nothing more was sent.");

    const d = hold(fakeCore({ where: "cloud", provider: "openai", model: "o-big", cap: 3, kept: { openai: true } }), "DELETE /v1/toolsmith/keys/openai");
    const u = tileOf(await page(d, ""));
    button(u, "Take the key out").fire("click");
    await flush();
    assert.strictEqual(saidOf(u, "toolsmith-key"), "Taking it out...");
    allHeld(u, true);
    await d.let(new Error("boom"));
    allHeld(u, false);
    assert.strictEqual(saidOf(u, "toolsmith-key"), "That did not finish. Nothing more was sent.");

    const e = hold(fakeCore({ where: "cloud", provider: "openai", model: "o-big", cap: 3 }), "PUT /v1/toolsmith/model");
    const sec = await page(e, "");
    const v = tileOf(sec);
    await choose(v, "");
    button(v, "Save").fire("click");
    await flush();
    assert.strictEqual(saidOf(v, "toolsmith"), "Saving...");
    assert.strictEqual(pickOf(v).disabled, true);
    assert.strictEqual(button(v, "Save").disabled, true);
    await e.let();
    assert.strictEqual(saidOf(tileOf(sec), "toolsmith"), "Saved: Toolsmith uses this computer.");
  }

  /* ---- no list of companies: nothing can be changed, and never a quiet move to this computer ---- */
  for (const where of ["cloud", "local"]) {
    const c = fakeCore(where === "cloud" ? { where: "cloud", provider: "openai", model: "o-big", cap: 3, kept: { openai: true } } : {});
    const real = c.api;
    c.api = async (m, p, b) => (m === "GET" && p === "/v1/toolsmith/providers" ? { status: 500, data: { error: "down" } } : real(m, p, b));
    const sec = await page(c, "");
    const t = tileOf(sec);
    assert.strictEqual(one(t, attr("data-toolsmith", "no-companies")).textContent, "Can't read the companies just now, so nothing can be changed here.");
    assert.strictEqual(button(t, "Save").disabled, true);
    assert.strictEqual(pickOf(t).disabled, true);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.strictEqual(c.where, where);
    assert.strictEqual(keyBoxOf(t), null);
    if (where === "cloud") assert.ok(/OpenAI, o-big/.test(nowOf(t)) && statOf(t) === "KEY KEPT");
  }

  /* ---- a blank paste says so and moves nothing ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = "   ";
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(c.calls, []);
    assert.strictEqual(saidOf(t, "toolsmith"), "That key is empty.");
    assert.strictEqual(c.where, "local");
    assert.strictEqual(tileOf(sec), t);
    assert.strictEqual(keyBoxOf(t).value, "");
    allHeld(t, false);
    const k = fakeCore({ where: "cloud", provider: "anthropic", model: "a-big", cap: 5, kept: { anthropic: true } });
    const u = tileOf(await page(k, ""));
    keyBoxOf(u).value = "\t ";                                  // the same company with no tick: no cap call either
    button(u, "Save").fire("click");
    await flush();
    assert.deepStrictEqual(k.calls, []);
    assert.strictEqual(saidOf(u, "toolsmith"), "That key is empty.");
  }

  /* ---- Bridge can't be read: the Saved or inbox line still shows ---- */
  for (const vault of ["open", "locked"]) {
    const c = fakeCore({ vault });
    const real = c.api;
    c.api = async (m, p, b) => (m === "GET" && p === "/v1/bridge" ? { status: 500, data: { error: "down" } } : real(m, p, b));
    const sec = await page(c, "bridge");
    const t = tileOf(sec);
    await choose(t, "anthropic");
    keyBoxOf(t).value = KEY;
    tick(t);
    button(t, "Save").fire("click");
    await flush();
    const line = one(sec, attr("data-said", "bridge")).textContent;
    assert.ok(/Saved: Anthropic/.test(line), line);
    assert.ok(vault === "open" ? /Saved in your vault/.test(line) : /inbox/.test(line), line);
    noKeyAnywhere(sec, c);
  }

  /* ---- Toolsmith can't be read: the corner says CAN'T TELL, and a Saved line coming in stays ---- */
  {
    const c = fakeCore();
    const sec = await page(c, "");
    const t = tileOf(sec);
    await choose(t, "openai");
    tick(t);
    const real = c.api;
    c.api = async (m, p, b) => (m === "GET" && p === "/v1/toolsmith" ? { status: 500, data: { error: "Your bot did not answer." } } : real(m, p, b));
    button(t, "Save").fire("click");
    await flush();
    const u = tileOf(sec);
    assert.strictEqual(statOf(u), "CAN'T TELL");
    assert.ok(/Your bot did not answer/.test(u.textContent));
    assert.ok(/Saved: OpenAI/.test(saidOf(u, "toolsmith")));
  }

  /* ---- a tile that can't read Toolsmith says so, and the rest of the Overview still draws ---- */
  {
    const c = fakeCore();
    const real = c.api;
    c.api = async (m, p, body) => (p === "/v1/toolsmith" ? { status: 500, data: { error: "Your bot did not answer." } } : real(m, p, body));
    const sec = await page(c, "");
    assert.ok(/Your bot did not answer/.test(tileOf(sec).textContent));
    assert.ok(one(sec, attr("data-tile", "github")) && one(sec, attr("data-tile", "zone-vault")));
    assert.strictEqual(keyBoxOf(tileOf(sec)), null);
    assert.strictEqual(statOf(tileOf(sec)), "CAN'T TELL");
  }

  assert.deepStrictEqual(spied.filter((s) => s.indexOf(KEY) >= 0), []);
  console.log("ok");
})().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
