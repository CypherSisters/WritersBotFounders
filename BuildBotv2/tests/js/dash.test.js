// Lane dash (D82): Home shows the installed apps by itself. Run by tests/test_dash.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
const BASE = "http://127.0.0.1:8422";

const rows = [
  { id: "freshtable", name: "FreshTable", kind: "app", status: "running", own: true, origin: "made", health: { ok: true },
    ui: { pages: [{ id: "main", path: "/", show: "in_desktop" }], tray: [{ label: "FreshTable", opens: "main" }, { label: "Lists", opens: "main" }] } },
  { id: "worldmonitor", name: "World Monitor", kind: "app", status: "stopped", origin: "adopted:https://github.com/x/y",
    ui: { pages: [{ id: "main", url: "http://127.0.0.1:18500/", show: "new_tab" }] } },
  { id: "paint", name: "Paint", kind: "app", status: "running", origin: "made", ui: { open: { program: "paint.exe" } } },
  { id: "look-clean", name: "Clean", kind: "look", status: "running", origin: "store:verified", ui: {} },
  { id: "notes-tool", name: "Notes", kind: "tool", status: "running", origin: "made", ui: {} },
  { id: "gone", name: "Gone", kind: "app", status: "removed", origin: "made", ui: { pages: [{ id: "m", path: "/" }] } },
];

// one launcher per app with a page or a program; looks, page-less tools and removed ones are not apps on Home
const apps = L.homeApps(rows, BASE);
assert.deepStrictEqual(apps.map((a) => a.pkg), ["freshtable", "worldmonitor", "paint"]);
assert.deepStrictEqual(apps.map((a) => a.page), [true, true, false]);
assert.strictEqual(apps[0].name, "FreshTable");
assert.strictEqual(apps[0].target.kind, "desktop");          // built for PuffPAI: inside the Desktop (D13)
assert.strictEqual(apps[1].target.kind, "tab");              // an outside app: a new tab (D13)
assert.strictEqual(apps[1].running, false);
assert.deepStrictEqual(L.homeApps([], BASE), []);

// page tiles: hers first (newest first), all apps with a page when apps show as pages, never a page-less one
assert.deepStrictEqual(L.appPageIds(apps, { apps: { view: "tiles", pages: [] } }), []);
assert.deepStrictEqual(L.appPageIds(apps, { apps: { view: "tiles", pages: ["worldmonitor", "paint", "gone"] } }), ["worldmonitor"]);
assert.deepStrictEqual(L.appPageIds(apps, { apps: { view: "pages", pages: ["worldmonitor"] } }), ["worldmonitor", "freshtable"]);
assert.deepStrictEqual(L.appPageIds(apps, {}), []);
// taken off Home in "pages" view (hidden): no tile, so the buttons say "Show on Home" again
assert.deepStrictEqual(L.appPageIds(apps, { apps: { view: "pages", pages: [] }, tiles: { hidden: ["app/freshtable"] } }), ["worldmonitor"]);
assert.strictEqual(L.APP_TILE, "app/");

// the last check before a frame (D83): only a package's own origin for the pages address, never an outside
// app, never the Desktop's own origin, never a sign-in
const HERE = "http://127.0.0.1:8420/#home";
["http://freshtable.localhost:8422/", "http://freshtable.localhost:8422/lists?x=1"]
  .forEach((u) => assert.strictEqual(L.frameSrcOk(u, HERE, BASE), true, u));
["http://127.0.0.1:18500/", "http://localhost:18500/", "http://[::1]:18500/", "https://127.0.0.1:18500/",
 "http://freshtable.localhost:9999/", "https://freshtable.localhost:8422/", "http://puffpai.localhost:8420/",
 "http://a.b.localhost:8422/", "http://user:pw@freshtable.localhost:8422/", "http://127.0.0.1:8420/",
 "http://127.0.0.1:8422/", "https://example.com/", "http://10.0.0.5:8080/", "javascript:alert(1)",
 "file:///C:/x.html", "data:text/html,x", "", null, "http://evil.com.localhost.example/",
 "http://127.0.0.1.example.com/", "http://Freshtable.localhost:8422.example/"]
  .forEach((u) => assert.strictEqual(L.frameSrcOk(u, HERE, BASE), false, String(u)));
// no pages address known, or no Desktop origin to compare with: nothing frames
assert.strictEqual(L.frameSrcOk("http://freshtable.localhost:8422/", HERE, ""), false);
assert.strictEqual(L.frameSrcOk("http://freshtable.localhost:8422/", HERE, undefined), false);
assert.strictEqual(L.frameSrcOk("http://freshtable.localhost:8422/", "", BASE), false);
// a Desktop on a package-looking host never frames itself
assert.strictEqual(L.frameSrcOk("http://puffpai.localhost:8422/", "http://puffpai.localhost:8422/#home", BASE), false);
assert.strictEqual(L.frameSrcOk("http://freshtable.localhost:8422/", "http://puffpai.localhost:8420/", BASE), true);

// a frame's rights: never top navigation, nothing unknown
assert.strictEqual(L.frameSandbox("allow-scripts allow-top-navigation allow-same-origin allow-modals allow-scripts"),
  "allow-scripts allow-same-origin");
assert.strictEqual(L.frameSandbox(L.PAGE_SANDBOX), L.PAGE_SANDBOX);
assert.strictEqual(L.frameSandbox("allow-top-navigation-by-user-activation"), "");
assert.strictEqual(L.frameSandbox(undefined), "");

// putting Home back in order moves as little as can be, and a framed tile last of all (a moved frame reloads)
assert.deepStrictEqual(L.stayers([2, 0, 1], [1000, 1000, 1000]), [1, 2]);    // F1 moved to last: F2, F3 stay
assert.deepStrictEqual(L.stayers([1, 0], [1, 1000]), [1]);                    // A moved below frame F: F stays
assert.deepStrictEqual(L.stayers([0, 1, 2, 3], [1, 1000, 1, 1]), [0, 1, 2, 3]);
assert.deepStrictEqual(L.stayers([], []), []);

console.log("dash ok");
