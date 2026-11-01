// D130: the Desktop on the home network: each package's own port, and links that mean the right computer.
// Run by tests/test_lan_pairing.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const loc = { protocol: "http:", hostname: "192.168.1.5" };
const base = L.networkBase(loc, { ports: { "made-app": 19101, "other-app": 19102 } });
const LOCAL = "http://127.0.0.1:8422";

assert.strictEqual(L.networkBase(loc, null), "");
assert.strictEqual(L.networkBase(loc, { ports: null }), "");
assert.strictEqual(L.isNetwork(base), true);
assert.strictEqual(L.isNetwork(LOCAL), false);
assert.strictEqual(L.isNetwork(""), false);

// A package's own origin: its own port, on the address this device uses. Never <id>.localhost on a phone.
assert.strictEqual(L.pkgOrigin("made-app", base), "http://192.168.1.5:19101");
assert.strictEqual(L.pkgOrigin("not-here", base), null);
assert.strictEqual(L.pkgOrigin("constructor", base), null);
assert.strictEqual(L.pkgOrigin("__proto__", base), null);
assert.strictEqual(L.pkgOrigin("made-app", L.networkBase({ protocol: "http:", hostname: "mybox.local" }, { ports: { "made-app": 19101 } })), "http://mybox.local:19101");
// On this computer, as it was.
assert.strictEqual(L.pkgOrigin("made-app", LOCAL), "http://made-app.localhost:8422");

const own = { id: "made-app", own: true, name: "Made", status: "running", ui: { pages: [{ id: "home", path: "/" }] } };
const adopted = { id: "outside-app", origin: "adopted:https://example.test/x", name: "Outside", status: "running", port: 18500,
  ui: { pages: [{ id: "home", url: "http://127.0.0.1:18500/" }] } };
const adoptedFiles = { id: "other-app", origin: "adopted:https://example.test/y", name: "Files", ui: { pages: [{ id: "home", path: "/" }] } };
const prog = { id: "gui", ui: { open: { program: "x.exe" } } };
const web = { id: "made-app", own: false, ui: { pages: [{ id: "site", url: "https://example.test/" }] } };

// Pages: one of ours in the Desktop, an outside one says where it opens.
assert.deepStrictEqual(L.pageTarget(own, own.ui.pages[0], base), { kind: "desktop", src: "http://192.168.1.5:19101/", page: "home" });
const t = L.pageTarget(adopted, adopted.ui.pages[0], base);
assert.strictEqual(t.kind, "computer");
assert.strictEqual(t.note, L.ON_COMPUTER);
assert.ok(!JSON.stringify(t).includes("127.0.0.1"));
assert.strictEqual(L.pageTarget(adoptedFiles, adoptedFiles.ui.pages[0], base).kind, "computer");
assert.strictEqual(L.pageTarget(prog, null, base).kind, "computer");
assert.strictEqual(L.pageTarget(web, web.ui.pages[0], base).kind, "tab");   // a real website opens on the phone too
// On this computer, as it was.
assert.strictEqual(L.pageTarget(adopted, adopted.ui.pages[0], LOCAL).kind, "tab");
assert.strictEqual(L.pageTarget(prog, null, LOCAL).kind, "program");

// The links the Core makes: never the phone's own 127.0.0.1.
for (const u of ["http://127.0.0.1:18500/", "http://localhost:18500/x", "http://[::1]:18500/", "http://not-here.localhost:8422/", "not a url"]) {
  const o = L.networkOpen(u, base);
  assert.strictEqual(o.url, null, u);
  assert.strictEqual(o.line, L.ON_COMPUTER);
}
assert.deepStrictEqual(L.networkOpen("http://made-app.localhost:8422/a?b=1#c", base), { url: "http://192.168.1.5:19101/a?b=1#c" });
assert.deepStrictEqual(L.networkOpen("https://example.test/", base), { url: "https://example.test/" });
assert.deepStrictEqual(L.networkOpen("http://127.0.0.1:18500/", LOCAL), { url: "http://127.0.0.1:18500/" });

// A frame: exactly a package's own port, never the Desktop's own origin or another port.
const here = "http://192.168.1.5:8423/";
assert.strictEqual(L.frameSrcOk("http://192.168.1.5:19101/", here, base), true);
assert.strictEqual(L.frameSrcOk("http://192.168.1.5:8423/", here, base), false);
assert.strictEqual(L.frameSrcOk("http://192.168.1.5:19999/", here, base), false);
assert.strictEqual(L.frameSrcOk("http://evil.example:19101/", here, base), false);
assert.strictEqual(L.frameSrcOk("http://x:y@192.168.1.5:19101/", here, base), false);
assert.strictEqual(L.frameSrcOk("http://made-app.localhost:8422/", here, base), false);
const ownBase = L.networkBase({ protocol: "http:", hostname: "192.168.1.5" }, { ports: { "desk": 8423 } });
assert.strictEqual(L.frameSrcOk("http://192.168.1.5:8423/", here, ownBase), false);
assert.strictEqual(L.frameSrcOk("http://made-app.localhost:8422/", "http://puffpai.localhost:8420/", LOCAL), true);

// Which app a link means.
const rows = [own, adopted];
assert.strictEqual(L.openMatch("http://192.168.1.5:19101/page", rows, base), own);
assert.strictEqual(L.openMatch("http://192.168.1.5:19999/", rows, base), null);
assert.strictEqual(L.openMatch("http://made-app.localhost:8422/", rows, LOCAL), own);

// The Settings row says what opens, and that there is no TLS, in the Core's own words.
const off = L.networkView({ on: false, urls: ["http://192.168.1.5:8423/"] }, true);
assert.strictEqual(off.on, false);
assert.ok(/Only this computer/.test(off.line) && /only a device you pair/.test(off.line));
assert.strictEqual(off.button, "Turn it on");
assert.deepStrictEqual(off.urls, []);
assert.ok(/firewall/.test(off.how));
const on = L.networkView({ on: true, open: true, urls: ["http://192.168.1.5:8423/"], worth_knowing: "there is no TLS" }, true);
assert.deepStrictEqual(on.urls, ["http://192.168.1.5:8423/"]);
assert.strictEqual(on.worth, "there is no TLS");
assert.ok(/firewall/.test(on.how));
assert.ok(/only a device you pair/.test(on.line) && /Adopted apps/.test(on.line));
assert.strictEqual(on.button, "Turn it off");
assert.ok(/cuts off this device/.test(L.networkView({ on: true, open: true }, false).how));
assert.strictEqual(L.networkView({ on: true, open: false, line: "Another program is using port 8423." }, true).line, "Another program is using port 8423.");

// Signing in on the network: a code from the computer. No folder, no command.
const w = L.signInWords({ file: "", folder: "", pair: "/pair" });
assert.ok(/pairing code from the computer/.test(w.line));
assert.ok(!/cmd|terminal/.test(w.line));
assert.ok(/another device/.test(L.signInWords({ pair: "/pair" }, "", true).line));
assert.ok(/double-click/.test(L.signInWords({ file: "Sign in a browser.cmd", folder: "C:\\x" }).line));

// Nothing is stopped on its way out any more: a paired device gets everything (the owner's choice).
assert.strictEqual(L.staysOnComputer, undefined);
for (const h of ["192.168.1.5", "mybox.local", "mybox"]) assert.strictEqual(L.onNetworkHost(h), true, h);
for (const h of ["puffpai.localhost", "localhost", "127.0.0.1", "PUFFPAI.LOCALHOST"]) assert.strictEqual(L.onNetworkHost(h), false, h);

// A base from a page on this computer still draws nothing from the network's shape.
const items = L.trayItems([own, adopted], base);
assert.strictEqual(items.find((i) => i.pkg === "made-app").target.kind, "desktop");
assert.strictEqual(items.find((i) => i.pkg === "outside-app").target.kind, "computer");

console.log("ok");
