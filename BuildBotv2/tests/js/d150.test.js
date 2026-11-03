// D150: after a Core restart the window and the Desktop ask for the page ports again. The real listen and stream code,
// cut from the two files and run on a fake fetch: a connection that ends, then one that comes back.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const WEB = path.join(__dirname, "..", "..", "packages", "desktop", "web");
const read = (name) => fs.readFileSync(path.join(WEB, name), "utf8");

/* The text of one function, from its name to its closing brace. */
function cut(src, start) {
  const from = src.indexOf(start);
  assert.ok(from >= 0, start);
  let depth = 0;
  for (let i = src.indexOf("{", from); i < src.length; i++) {
    if (src[i] === "{") depth += 1;
    if (src[i] === "}" && --depth === 0) return src.slice(from, i + 1);
  }
  throw new Error("no end: " + start);
}

/* A stream that ends at once, *n* times, then one that stays open; every connection before that may fail first. */
function fakeFetch(plan, onEach) {
  let call = 0;
  return async () => {
    const step = plan[Math.min(call, plan.length - 1)];
    call += 1;
    if (onEach) onEach(call);
    if (step === "fail") throw new Error("down");
    if (step === "500") return { ok: false, status: 500 };
    return { ok: true, status: 200, body: { getReader: () => ({ read: step === "end"
      ? async () => ({ done: true }) : () => new Promise(() => {}) }) } };
  };
}

function sandbox(settings, plan, extra) {
  const asked = [];
  const box = { S: { settings, signedOut: false, subs: {} }, asked, loadSettings: () => { asked.push("again"); },
                authHeaders: (h) => h, signIn: () => { box.S.signedOut = true; }, fire: () => {}, TextDecoder, console,
                AbortController, Promise, JSON, EVENTS: [], fetch: fakeFetch(plan),
                setTimeout: (fn) => setImmediate(fn), ...extra };
  return box;
}

const settle = () => new Promise((ok) => setTimeout(ok, 60));

const windowListen = cut(read("window.js"), "function listen()");
const desktopSrc = read("desktop.js");
const desktopListen = cut(desktopSrc, "function stream(path)") + "\n" + cut(desktopSrc, "function listen()");

async function run(src, settings, plan) {
  const box = sandbox(settings, plan);
  vm.runInNewContext(src + "\nlisten();", box);
  await settle();
  box.S.signedOut = true;
  return box.asked.length;
}

(async () => {
  for (const [name, src] of [["window", windowListen], ["desktop", desktopListen]]) {
    const net = { network: true };
    // The first connection is the page's own load: it asked already.
    assert.strictEqual(await run(src, net, ["stay"]), 0, name + ": not at the first connection");
    // The Core went away and came back: once, as the stream returns.
    assert.strictEqual(await run(src, net, ["end", "stay"]), 1, name + ": once when it comes back");
    assert.strictEqual(await run(src, net, ["end", "fail", "500", "stay"]), 1, name + ": failed tries do not ask");
    // Each return asks again: a second restart closes the ports again.
    assert.strictEqual(await run(src, net, ["end", "end", "stay"]), 2, name + ": each return asks");
    // On the bot's own computer there are no page ports to ask for.
    assert.strictEqual(await run(src, { network: false }, ["end", "stay"]), 0, name + ": not on this computer");
    assert.strictEqual(await run(src, null, ["end", "stay"]), 0, name + ": not before settings are read");
  }
  // What it calls is the one place that asks for the ports and sets them.
  for (const name of ["window.js", "desktop.js"]) {
    const load = cut(read(name), "async function loadSettings()");
    assert.ok(load.includes('"/v1/network/pages"') && load.includes("L.networkBase(location, n.data)"), name);
  }
  console.log("ok");
})().catch((e) => { console.error(e); process.exit(1); });
