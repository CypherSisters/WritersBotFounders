// Headless checks of the Desktop's logic.js (no browser). Run by tests/test_desktop_js.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const ok = { ok: true, checked_at: "2026-10-22T10:00:00Z", reason: "" };
const rows = [
  { id: "freshtable", kind: "app", name: "FreshTable", own: true, origin: "made", status: "running", health: ok,
    ui: { pages: [{ id: "main", title: "FreshTable", path: "/", show: "in_desktop" }],
          tray: [{ label: "FreshTable", icon: "icons/pot.svg", opens: "main" }],
          widgets: [{ id: "list", type: "list", title: "Shopping list", path: "/widget/list" },
                    { id: "w", type: "weird", title: "Unknown", path: "/w" }] } },
  { id: "worldmonitor", kind: "app", name: "World Monitor", origin: "adopted:https://github.com/koala73/worldmonitor",
    status: "running", health: { ok: false, checked_at: null, reason: "Not checked yet." },
    ui: { pages: [{ id: "main", title: "World Monitor", path: "/", show: "in_desktop" }] } },
  { id: "notes-page", kind: "page", name: "My page", own: true, origin: "local:C:/x", status: "running", health: ok,
    ui: { pages: [{ id: "p", title: "Mine", file: "web/index.html", show: "in_desktop" }],
          widgets: [{ id: "h", type: "html", title: "Clock", file: "web/clock.html" }] } },
  { id: "gimp", kind: "app", name: "GIMP", origin: "made", status: "running", health: ok,
    ui: { open: { program: "C:/gimp.exe" } } },
  { id: "desktop", kind: "ui", name: "PuffPAI Desktop", origin: "core", status: "running", health: ok, ui: {} },
  { id: "cloud", kind: "avatar", name: "Cloud", origin: "core", status: "running", health: ok, ui: {} },
  { id: "gone", kind: "app", name: "Gone", own: true, origin: "made", status: "removed", health: ok,
    ui: { pages: [{ id: "m", title: "x", path: "/" }] } },
];

// D53: every page, icon and widget is on its package's own origin, never on the Core's own address
const PAGES = "http://127.0.0.1:8422";
const FT = "http://freshtable.localhost:8422";
assert.strictEqual(L.pkgOrigin("freshtable", PAGES), FT);
assert.strictEqual(L.pkgOrigin("Not-An-Id", PAGES), null);
assert.strictEqual(L.pkgOrigin("evil.example", PAGES), null);
assert.strictEqual(L.pkgOrigin("freshtable", ""), null);

// tray: built for PuffPAI -> in the Desktop; adopted -> a new tab even if it says in_desktop (D13)
const tray = L.trayItems(rows, PAGES);
assert.deepStrictEqual(tray.map((t) => t.pkg), ["freshtable", "worldmonitor", "notes-page", "gimp"]);
assert.deepStrictEqual(tray[0].target, { kind: "desktop", src: FT + "/", page: "main" });
assert.strictEqual(tray[0].icon, FT + "/icons/pot.svg");
assert.strictEqual(tray[1].target.kind, "tab");
assert.strictEqual(tray[1].target.url, "http://worldmonitor.localhost:8422/");
assert.strictEqual(tray[1].down, true);
assert.deepStrictEqual(tray[2].target, { kind: "desktop", src: "http://notes-page.localhost:8422/web/index.html", page: "p" });
assert.strictEqual(tray[3].target.kind, "program");
// an adopted app's button says where it opens (02 "Adopted packages"); a program says Open
assert.strictEqual(tray[1].openLabel, "Open in a new tab");
assert.strictEqual(tray[0].openLabel, null);
assert.strictEqual(tray[3].openLabel, "Open");
// only an adopted app says "Open in a new tab"; any other new-tab page keeps its own label (I2b fix)
const ownTab = { id: "mytab", kind: "app", name: "My Tab", own: true, origin: "made", status: "running", health: ok,
                 ui: { pages: [{ id: "m", title: "My Tab", path: "/", show: "new_tab" }] } };
const ownItem = L.trayItems([ownTab], PAGES)[0];
assert.strictEqual(ownItem.target.kind, "tab");
assert.strictEqual(ownItem.openLabel, null);
assert.strictEqual(ownItem.label, "My Tab");
assert.strictEqual(L.openLabel({ kind: "tab", url: "http://x.localhost:8422/" }), null);
assert.strictEqual(L.openLabel({ kind: "tab", url: "http://x.localhost:8422/" }, { origin: "local:C:/x" }), null);
assert.strictEqual(L.openLabel({ kind: "tab", url: "http://x.localhost:8422/" }, { origin: "adopted:https://github.com/a/b" }),
                   "Open in a new tab");
assert.strictEqual(L.openLabel({ kind: "tab", url: "u" }, { origin: "made-adopted:x" }), null);
// opening the Bridge: in safe mode the reason comes first, then the PIN (I2b decision)
assert.deepStrictEqual(L.bridgeOpen({ state: "closed", safe_mode: { on: false, reasons: [] } }),
                       { needsPin: false, title: null, lines: [] });
assert.deepStrictEqual(L.bridgeOpen(null).needsPin, false);
const safeOpen = L.bridgeOpen({ state: "closed", safe_mode: { on: true, reasons: ["The outside log was changed."] } });
assert.strictEqual(safeOpen.needsPin, true);
assert.strictEqual(safeOpen.title, "BuildBot is in safe mode");
assert.deepStrictEqual(safeOpen.lines, ["The outside log was changed.", L.BRIDGE_PIN_LINE]);
const refusedOpen = L.bridgeOpen({ needs_pin: true, safe_mode: { on: true, reasons: [] } });
assert.strictEqual(refusedOpen.needsPin, true);
assert.strictEqual(refusedOpen.lines[0], "A safety check failed.");
assert.ok(L.BRIDGE_PIN_LINE.indexOf("\u2014") < 0);
// tick boxes on lane I4b's cards: the plan card's offers and a job's hold card (lane I4a)
const planCard = L.consentCard({ consent_id: "c-plan", card_sha: "s", title: "Build this plan?", lines: ["Plan."],
  card: { scope: { kind: "package", target: "toolsmith-build-abc123" },
          covers: { build: "20261023-1-abc123", card: { offers: [{ job: 1, job_name: "Shopping", package: "freshtable",
            name: "FreshTable", tool: "freshtable.add_shopping",
            line: "Shopping: FreshTable may cover this: it would use add_shopping. Tick to use it instead of searching." }] } } } });
assert.strictEqual(planCard.ticks.kind, "offers");
assert.strictEqual(planCard.ticks.route, "/v1/toolsmith/builds/20261023-1-abc123/use");
assert.deepStrictEqual(planCard.ticks.items, [{ value: 1, label: "Shopping: FreshTable may cover this: it would use add_shopping. Tick to use it instead of searching.",
  store: false, package: "freshtable", name: "FreshTable", jobName: "Shopping" }]);
assert.deepStrictEqual(planCard.ticks.body([]), { consent_id: "c-plan", use: [] });
assert.deepStrictEqual(planCard.ticks.body([1]), { consent_id: "c-plan", use: [1] });
const holdCard = L.consentCard({ consent_id: "c-hold", card_sha: "s", title: "Let Toolsmith try these 2 apps?", lines: ["For: Dinner."],
  card: { build: "20261023-1-abc123", job: "20261023-1-abc123-2",
          try_holds: [{ candidate: "github:a/one", name: "One", url: "https://github.com/a/one" },
                      { candidate: "github:b/two", name: "Two", url: "https://github.com/b/two" }] } });
assert.strictEqual(holdCard.ticks.kind, "holds");
assert.strictEqual(holdCard.ticks.route, "/v1/toolsmith/builds/20261023-1-abc123/holds/c-hold");
assert.deepStrictEqual(holdCard.ticks.items.map(function (i) { return i.value; }), ["github:a/one", "github:b/two"]);
assert.deepStrictEqual(holdCard.ticks.body([]), { try: [] });         // nothing ticked: sent, so it tries none
assert.ok(holdCard.ticks.note.indexOf("nothing ticked, your Yes lets Toolsmith try none of them") > 0);
assert.strictEqual(L.consentCard({ consent_id: "x", title: "t", lines: ["a"], card: { name: "x" } }).ticks, null);
assert.strictEqual(L.consentCard({ consent_id: "x", title: "t", lines: [], card: holdCard }).ticks, null);  // no details: No only
// ticks not sent yet survive a redraw, and go when the card is answered or gone
const mem = new L.TickMemory();
mem.set("c-hold", "github:a/one", true);
mem.set("c-hold", "github:b/two", true);
mem.set("c-hold", "github:b/two", false);
mem.set("c-plan", 1, true);
mem.keep(["c-hold", "c-plan"]);
assert.deepStrictEqual(mem.get("c-hold"), ["github:a/one"]);
assert.ok(mem.has("c-plan", 1) && !mem.has("c-plan", 2));
mem.keep(["c-hold"]);                                                 // the plan card is gone
assert.deepStrictEqual(mem.get("c-plan"), []);
mem.drop("c-hold");                                                   // answered
assert.deepStrictEqual(mem.get("c-hold"), []);
// Installed list: a package with no process reads "ready" (the Core's process field)
const ready = L.installedRows([
  { id: "example-page", name: "My notes", kind: "page", status: "stopped", process: false, health: ok, ui: {} },
  { id: "freshtable", name: "FreshTable", kind: "app", status: "stopped", process: true, health: ok, ui: {} },
  { id: "old", name: "Old", kind: "app", status: "stopped", health: ok, ui: {} },
  { id: "ask", name: "Ask", kind: "app", status: "needs_you", process: true, health: ok, ui: {} }]);
assert.deepStrictEqual(ready.map(function (r) { return r.status; }), ["ready", "stopped", "stopped", "needs you"]);
// the Bridge in her words (lane I4a): search-only reads closed, and its button turns the search off
// (lane I6b: state and shown are one word; setting is the Bridge's own open or closed)
const searchOnly = L.bridgeView({ state: "closed", shown: "closed", setting: "open", search: true,
  said: ["The Bridge is closed. While Toolsmith builds, it may search GitHub; nothing else goes out."] });
assert.strictEqual(searchOnly.text, "The Bridge is closed. While Toolsmith builds, it may search GitHub; nothing else goes out.");
assert.strictEqual(searchOnly.button, "Turn off Toolsmith's search");
assert.strictEqual(L.bridgeView({ state: "open", shown: "open", setting: "open", said: ["The Bridge is open."] }).button, "Close the Bridge");
assert.strictEqual(L.bridgeView({ state: "closed" }).button, "Open the Bridge");
// while a build searches it reads open, in the Core's words, with the one Close button
const building = L.bridgeView({ state: "open", shown: "open", setting: "open", search: true,
  said: ["The Bridge is open while Toolsmith builds: it may search GitHub; nothing else goes out."] });
assert.strictEqual(building.text, "The Bridge is open while Toolsmith builds: it may search GitHub; nothing else goes out.");
assert.strictEqual(building.button, "Close the Bridge");
assert.strictEqual(L.bridgeView({ state: "closed", setting: "closed" }).button, "Open the Bridge");
// set open with nothing let through reads closed, and still offers Close: the setting itself is open (lane I8b)
const nothing = L.bridgeView({ state: "closed", shown: "closed", setting: "open", search: false,
  said: ["The Bridge is closed. Nothing leaves your computer without your consent."] });
assert.strictEqual(nothing.button, "Close the Bridge");
assert.strictEqual(nothing.action, "close");
assert.strictEqual(nothing.text, "The Bridge is closed. Nothing leaves your computer without your consent.");
assert.strictEqual(L.bridgeView(null).text, "The Bridge is closed.");
// D83: only the Core's own answer shows a page inside; a row with no answer, or a false one, opens in a tab.
assert.strictEqual(L.pageTarget({ id: "tt", origin: "made" }, { id: "x", path: "/" }, "http://127.0.0.1:8422").kind, "tab");
assert.strictEqual(L.pageTarget({ id: "tt", own: false, origin: "core" }, { id: "x", path: "/" }, "http://127.0.0.1:8422").kind, "tab");
assert.strictEqual(L.pageTarget({ id: "tt", own: "true" }, { id: "x", path: "/" }, "http://127.0.0.1:8422").kind, "tab");
assert.strictEqual(L.pageTarget({ id: "tt", own: true }, { id: "x", path: "/" }, "http://127.0.0.1:8422").kind, "desktop");
assert.deepStrictEqual(L.widgetsOf([{ id: "tt", name: "T", ui: { widgets: [{ id: "h", type: "html", title: "H", file: "h.html" }, { id: "l", type: "list", title: "L", file: "l.json" }] } }],
  "http://127.0.0.1:8422").map((w) => w.type), ["list"]);
assert.strictEqual(L.pageTarget(rows[0], { id: "x", title: "x", url: "http://127.0.0.1:3000/" }).kind, "tab");
// without a pages address no package link is made at all
assert.deepStrictEqual(L.trayItems(rows)[0].target, { kind: "none" });
assert.strictEqual(L.trayItems(rows)[0].icon, null);
assert.strictEqual(L.widgetsOf(rows, PAGES)[1].src, "http://notes-page.localhost:8422/web/clock.html");
assert.strictEqual(L.onPages("/pkg/cloud/cloud.svg", PAGES), "http://cloud.localhost:8422/cloud.svg");
assert.strictEqual(L.onPages("https://elsewhere.example/x.png", PAGES), null);
assert.strictEqual(L.PAGE_SANDBOX, "allow-scripts allow-forms allow-popups allow-same-origin");   // its own origin (D53)
assert.ok(L.WIDGET_SANDBOX.indexOf("allow-same-origin") < 0);
const iconRow = { id: "xy", kind: "app", name: "X", status: "running", health: ok,
                  ui: { pages: [{ id: "m", title: "X", path: "/" }], tray: [{ label: "X", icon: "javascript:alert(1)", opens: "m" }] } };
assert.strictEqual(L.trayItems([iconRow], PAGES)[0].icon, null);

// widgets by type; unknown types are left out; html goes to a sandboxed frame (desktop.js)
const widgets = L.widgetsOf(rows, PAGES);
assert.deepStrictEqual(widgets.map((w) => w.type), ["list", "html"]);
assert.strictEqual(widgets[0].src, FT + "/widget/list");
assert.deepStrictEqual(L.widgetModel("list", { items: [{ text: "milk" }, { text: "eggs" }] }), { items: ["milk", "eggs"] });
assert.deepStrictEqual(L.widgetModel("number", { value: 3, unit: "items", label: "On the list" }),
                       { value: "3", unit: "items", label: "On the list" });
assert.strictEqual(L.widgetModel("feed", { items: [{ title: "x", url: "javascript:alert(1)" }] }).items[0].url, null);
assert.strictEqual(L.widgetModel("list", { nope: 1 }).error, L.BAD_WIDGET);
assert.strictEqual(L.widgetModel("image", "junk").error, L.BAD_WIDGET);
// image widgets: only the package's own picture, on its own origin; anything else is never loaded (D53)
const ctx = { pkg: "freshtable", base: PAGES };
assert.deepStrictEqual(L.widgetModel("image", { src: "/pkg/freshtable/pics/a.png", caption: "Soup" }, ctx),
                       { src: FT + "/pics/a.png", caption: "Soup" });
assert.strictEqual(L.widgetModel("image", { src: FT + "/a.png" }, ctx).src, FT + "/a.png");
["https://apod.nasa.gov/a.jpg", "//evil.example/a.png", "http://127.0.0.1:9999/a.png", "/pkg/other/a.png",
 "http://other.localhost:8422/a.png", "http://127.0.0.1:8422/pkg/freshtable/a.png", FT + "@evil.example/a.png",
 "http://freshtable.localhost:9999/a.png", FT + "/../x.png", FT + "//evil.example/a.png",
 "/pkg/freshtable/../other/a.png", "/pkg/freshtable/%2e%2e/other/a.png", "/pkg/freshtable/a\\..\\b.png",
 "/pkg/freshtable//evil.example/a.png", "data:image/png;base64,AAAA", "/v1/registry", "pics/a.png"].forEach((src) => {
  const m = L.widgetModel("image", { src: src, caption: "x" }, ctx);
  assert.strictEqual(m.src, null, src);
  assert.strictEqual(m.outside, true, src);
  assert.strictEqual(m.note, "This picture is outside your computer.", src);
});
assert.strictEqual(L.widgetModel("image", { src: "/pkg/freshtable/a.png" }).src, null);   // no pages address known

// "Your turn" cards: Copy, Run as administrator (admin only), Done, Skip
const docker = L.stepCard({ id: "docker", package: "worldmonitor", kind: "run_command", shell: "powershell", admin: true,
                            title: "Install Docker Desktop", why: "World Monitor runs in Docker.",
                            text: "winget install -e --id Docker.DockerDesktop" });
assert.deepStrictEqual(docker.buttons.map((b) => b.label), ["Copy", "Run as administrator, with my PIN", "Done", "Skip"]);
assert.strictEqual(docker.text, "winget install -e --id Docker.DockerDesktop");
assert.strictEqual(docker.from, "worldmonitor");
assert.strictEqual(docker.needsPin, true);
assert.deepStrictEqual(L.adminAnswer("482913"), { factor: "pin", pin: "482913" });
// the card says who raised it; a step a package raised itself never offers Run as administrator (D49)
const approved = L.stepCard({ id: "s1", package: "worldmonitor", raised_by: "setup_steps:worldmonitor", kind: "run_command",
                              admin: true, text: "winget install x", title: "t" });
assert.strictEqual(approved.raisedBy, "From worldmonitor's setup steps, which you said Yes to");
assert.strictEqual(approved.text, "winget install x");
const sneaky = L.stepCard({ id: "s2", package: "evil", raised_by: "pkg:evil", kind: "run_command", admin: true,
                            text: "whoami", title: "t" });
assert.strictEqual(sneaky.raisedBy, "Raised by the package evil");
assert.deepStrictEqual(sneaky.buttons.map((b) => b.label), ["Copy", "Done", "Skip"]);
assert.strictEqual(sneaky.needsPin, false);
assert.strictEqual(L.stepCard({ id: "s3", kind: "confirm", title: "t", raised_by: "toolsmith", package: "toolsmith" }).raisedBy,
                   "Raised by toolsmith");
assert.strictEqual(L.stepCard({ id: "s4", kind: "run_command", admin: true, shell: "cmd", text: "dir", title: "t",
                                raised_by: "core" }).buttons.map((b) => b.id).indexOf("admin"), -1);
const plain = L.stepCard({ id: "p", kind: "run_command", text: "dir", title: "t" });
assert.deepStrictEqual(plain.buttons.map((b) => b.label), ["Copy", "Done", "Skip"]);
const key = L.stepCard({ id: "k", kind: "enter_secret", key: "NASA_API_KEY", get_it: "https://api.nasa.gov", title: "Paste" });
assert.deepStrictEqual(key.buttons.map((b) => b.label), ["Get it", "Save to vault", "Done", "Skip"]);
assert.strictEqual(key.secret.key, "NASA_API_KEY");

// Yes cards: floor-level ones ask for the PIN, never a tap (D29)
const floor = L.consentCard({ id: "c1", title: "Install a provider that reads raw data", text: "It reads your chats.", floor: true });
assert.strictEqual(floor.needsPin, true);
assert.deepStrictEqual(L.consentAnswer(floor, true, "482913"), { answer: "yes", factor: "pin", pin: "482913" });
const everyday = L.consentCard({ id: "c2", title: "Add FreshTable", lines: ["Runs on this computer."] });
assert.deepStrictEqual(L.consentAnswer(everyday, true), { answer: "yes", factor: "tap" });
assert.deepStrictEqual(L.consentAnswer(everyday, false), { answer: "no", factor: "tap" });
const shown = L.consentCard({ consent_id: "c3", title: "Add X", card_sha: "ab".repeat(32) });
assert.deepStrictEqual(L.consentAnswer(shown, true), { answer: "yes", factor: "tap", card_sha: "ab".repeat(32) });
assert.deepStrictEqual(L.consentAnswer(shown, false), { answer: "no", factor: "tap", card_sha: "ab".repeat(32) });

// the Installed list
const inst = L.installedRows(rows);
assert.strictEqual(inst.length, 6);
assert.strictEqual(inst.find((r) => r.id === "worldmonitor").health, "Not checked yet");
assert.strictEqual(inst.find((r) => r.id === "worldmonitor").adopted, true);
assert.strictEqual(L.installedRows(rows, PAGES).find((r) => r.id === "worldmonitor").tab, "http://worldmonitor.localhost:8422/");
assert.strictEqual(L.installedRows(rows, PAGES).find((r) => r.id === "freshtable").tab, null);
assert.strictEqual(inst.find((r) => r.id === "worldmonitor").tab, null);      // no pages address: no link

// the Setup transcript: the shown-once code leaves the page after `clear`; hidden input never shows
const t = new L.WizardTranscript();
t.push({ title: "Vault", status: "show", lines: [], show: { value: "CODE-1234", line: "Shown once." },
         field: { id: "recovery_confirm", label: "Type it back", hidden: true } });
t.typed({ id: "recovery_confirm", hidden: true }, "CODE-1234");
assert.ok(t.visibleText().indexOf("CODE-1234") >= 0);
// a new code takes the old one's place on the page (D106)
const t2 = new L.WizardTranscript();
t2.push({ title: "Vault", status: "show", lines: [], show: { value: "OLD-CODE", line: "Shown once." } });
t2.push({ title: "Vault", status: "show", lines: [], show: { value: "NEW-CODE", line: "Shown once." } });
assert.strictEqual(t2.visibleText().indexOf("OLD-CODE"), -1);
assert.ok(t2.visibleText().indexOf("NEW-CODE") >= 0);
t.push({ title: "Vault", status: "ask", clear: true, lines: ["BuildBot removed the code from this page."],
         field: { id: "data_folder", label: "Your data folder" } });
assert.strictEqual(t.visibleText().indexOf("CODE-1234"), -1);
t.typed({ id: "data_folder" }, "D:/Data");
assert.ok(t.visibleText().indexOf("D:/Data") >= 0);
assert.deepStrictEqual(L.wizardInput({ type: "secret", hidden: true }), { kind: "password" });

// ===== lane I8b: the v1 dashboard's pages =====
// day 07:00 to 19:00, night otherwise; her pick wins
assert.strictEqual(L.themeFor(6, "auto"), "night");
assert.strictEqual(L.themeFor(7, "auto"), "day");
assert.strictEqual(L.themeFor(18, undefined), "day");
assert.strictEqual(L.themeFor(19, "auto"), "night");
assert.strictEqual(L.themeFor(12, "night"), "night");
assert.strictEqual(L.themeFor(2, "day"), "day");

// the rail: only pages every bot has (lane I10a); Setup until it is finished
const g = L.railGroups();
assert.deepStrictEqual(g.primary.map((r) => r.id), ["home", "chat", "apps", "building", "yourturn"]);
assert.deepStrictEqual(g.secondary.map((r) => r.id), ["knowledge", "vault", "people", "agents", "outside"]);
assert.deepStrictEqual(g.settings.map((r) => r.id), ["setup", "looks", "developer", "settings"]);
assert.deepStrictEqual(L.railGroups(L.RAIL, { setupDone: true }).settings.map((r) => r.id), ["looks", "developer", "settings"]);
assert.deepStrictEqual(L.railGroups(L.RAIL, { setupDone: false }).settings.map((r) => r.id), ["setup", "looks", "developer", "settings"]);
["smarthome", "talk", "device"].forEach((id) => assert.strictEqual(L.RAIL.find((x) => x.id === id), undefined));
assert.strictEqual(L.LATER_PAGES, undefined);

// lane I10a: the PuffPAI store, apps and pages only, in the order the route gives
const SVG = "data:image/svg+xml;base64,PHN2Zy8+";
const avail = [
  { id: "freshtable", name: "FreshTable", kind: "app", description: "Recipes and a pantry. Runs here.", version: "1.0.0", author: "Cypher Sisters", icon: SVG },
  { id: "cloud-avatar", name: "Puff", kind: "avatar", description: "The cloud.", icon: null },
  { id: "notes-page", name: "My notes", kind: "page", description: "A notes pad", icon: "https://example.org/x.svg" },
  { id: "example-page", name: "My notes (an example page you can copy)", kind: "page", description: "An example page." },
  { id: "trivia", name: "Trivia", kind: "app", description: "", icon: "data:text/html;base64,PGI+" },
  { id: "desktop", name: "Desktop", kind: "ui", description: "x" }, null, { kind: "app" },
];
const store = L.storeApps(avail);
assert.deepStrictEqual(store.map((p) => p.id), ["freshtable", "notes-page", "trivia"]);   // lane I18c: never the developer example
assert.strictEqual(store[0].icon, SVG);
assert.strictEqual(store[1].icon, null);                    // never a picture from outside
assert.strictEqual(store[2].icon, null);                    // only pictures
assert.strictEqual(store[0].line, "Recipes and a pantry.");
assert.strictEqual(store[0].about, "Recipes and a pantry. Runs here. By Cypher Sisters, version 1.0.0.");
assert.strictEqual(store[1].line, "A notes pad");
assert.strictEqual(L.STORE_LINE, "Anyone can build an app that works on BuildBot.");
// Home's question: only after setup, not after Not now, never with nothing left; at most 6
const setupDone = { wizard_complete: true };
assert.strictEqual(L.storePrompt({ wizard_complete: false }, {}, avail).show, false);
assert.strictEqual(L.storePrompt(setupDone, {}, avail).show, true);
assert.strictEqual(L.storePrompt(setupDone, { tiles: { hidden: ["store"] } }, avail).show, false);
assert.strictEqual(L.storePrompt(setupDone, {}, [avail[1]]).show, false);
assert.strictEqual(L.storePrompt(setupDone, {}, []).show, false);
const many = Array.from({ length: 9 }, (_, i) => ({ id: "a" + i, name: "A" + i, kind: "app" }));
assert.strictEqual(L.storePrompt(setupDone, {}, many).apps.length, 6);
const nn = L.storeNotNow({ tiles: { order: ["brain"], hidden: ["bridge", "store"], pinned: ["brain"], names: { brain: "Mind" } } });
assert.deepStrictEqual(nn, { tiles: { order: ["brain"], hidden: ["bridge", "store"], pinned: ["brain"], names: { brain: "Mind" } } });
assert.deepStrictEqual(L.storeNotNow({}).tiles.hidden, ["store"]);
assert.deepStrictEqual(L.storeNotNow(null).tiles, { order: [], hidden: ["store"], pinned: [], names: {} });

// lane I10a: the bot's name ("Puff" until she names it) and theme
assert.strictEqual(L.botName(null), "Puff");
assert.strictEqual(L.botName({ bot: { name: "  " } }), "Puff");
assert.strictEqual(L.botName({ bot: { name: " Wedding  Bot " } }), "Wedding Bot");
assert.strictEqual(L.helloLine(20, { bot: { name: "Juniper" } }), "Good evening, I'm Juniper.");
assert.strictEqual(L.helloLine(9, {}), "Good morning, I'm Puff.");
assert.strictEqual(L.helloLine(13, {}), "Good afternoon, I'm Puff.");
assert.deepStrictEqual(L.themeTokens({ tokens: { "--pink": "#ff00aa", "--bg": "rgba(1, 2, 3, 0.5)", "color": "red",
  "--x": "url(https://example.org/a.png)", "--y": "red; background: blue", "--z": "@import x", "--w": "</style>", "--n": 4,
  "--v": "a\\62 c" } }), { "--pink": "#ff00aa", "--bg": "rgba(1, 2, 3, 0.5)", "--n": "4" });
assert.deepStrictEqual(L.themeTokens(null), {});
const own = L.themeList(null);                               // no themes route: the Desktop's own palettes
assert.deepStrictEqual(own.map((t) => [t.id, t.mode, t.tokens]), [["puffpai", "both", {}]]);
assert.deepStrictEqual(L.themeList({ error: "No such route" }).map((t) => t.id), ["puffpai"]);
const themes = L.themeList([{ id: "rose", name: "Rose", mode: "day", tokens: { "--pink": "#e05" } }, { id: "ink", mode: "odd" }, { name: "no id" }]);
assert.deepStrictEqual(themes.map((t) => [t.id, t.name, t.mode, t.modeWords]), [["rose", "Rose", "day", "For day"], ["ink", "ink", "both", "Day and night"]]);
assert.strictEqual(L.themeById(themes, "ink").id, "ink");
assert.strictEqual(L.themeById(themes, "gone").id, "rose");
assert.strictEqual(L.themeById([], "x"), null);
// lane I14: a "both" theme sets its night tokens at night and its day tokens by day (the Core's day_tokens)
const both = L.themeList([{ id: "puff", mode: "both", tokens: { "--bg": "#0B0714" }, day_tokens: { "--bg": "#CFE9F9", "--x": "url(a)" } },
  { id: "dark", mode: "night", tokens: { "--bg": "#000" }, day_tokens: { "--bg": "#fff" } },
  { id: "light", mode: "day", tokens: { "--bg": "#fff" } }]);
assert.deepStrictEqual(both[0].dayTokens, { "--bg": "#CFE9F9" });           // checked like tokens
assert.deepStrictEqual(L.themeTokensFor(both[0], "night"), { "--bg": "#0B0714" });
assert.deepStrictEqual(L.themeTokensFor(both[0], "day"), { "--bg": "#CFE9F9" });
assert.deepStrictEqual(L.themeTokensFor(both[1], "day"), { "--bg": "#000" }); // night-only: whatever the hour
assert.deepStrictEqual(L.themeTokensFor(both[2], "night"), { "--bg": "#fff" }); // day-only: whatever the hour
assert.deepStrictEqual(L.themeTokensFor(L.themeList([{ id: "b", mode: "both", tokens: { "--bg": "#111" } }])[0], "day"), {});
assert.deepStrictEqual(L.themeTokensFor(null, "day"), {});
{ // theme.js itself: the day/night button re-applies a "both" theme's tokens, a night-only theme stays
  const vm = require("vm"), fs = require("fs");
  const props = {}, attrs = {}, store = {};
  const html = { style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } },
                 setAttribute: (k, v) => { attrs[k] = v; }, getAttribute: (k) => attrs[k] };
  const box = { window: { PuffLogic: L }, document: { documentElement: html }, setInterval: () => 0, JSON: JSON, Date: Date,
                localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } } };
  vm.createContext(box);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "packages", "desktop", "web", "theme.js"), "utf8"), box);
  const T = box.window.PuffTheme;
  T.set("night"); T.look(both[0], true);
  assert.strictEqual(attrs["data-puff-theme"], "night"); assert.strictEqual(props["--bg"], "#0B0714");
  T.set("day");
  assert.strictEqual(attrs["data-puff-theme"], "day"); assert.strictEqual(props["--bg"], "#CFE9F9");
  T.set("night"); assert.strictEqual(props["--bg"], "#0B0714");
  T.look(both[1], false); T.set("day"); assert.strictEqual(props["--bg"], "#000");
  T.tokens({ "--pink": "#123456" }, false); assert.deepStrictEqual(Object.keys(props), ["--pink"]);
  assert.strictEqual(JSON.parse(store["puffpai.themeLook"]).dayTokens["--bg"], "#CFE9F9");   // kept for the next visit
}
assert.strictEqual(new Set(L.RAIL.map((r) => r.id)).size, L.RAIL.length);
const later = L.comingLater("PUT /v1/model", "Setup changes it.");
assert.strictEqual(later.title, "Coming in a later build");
assert.strictEqual(later.line, "Not in this build: PUT /v1/model.");

// job status in plain words (03 3.6)
assert.strictEqual(L.jobWords("building: Mealie"), "Trying Mealie");
assert.strictEqual(L.jobWords("waiting_yes"), "Waiting for your Yes");
assert.strictEqual(L.jobWords("said_no"), "You said No");
assert.strictEqual(L.jobWords("odd_state"), "odd state");
assert.strictEqual(L.jobActive("building: x"), true);
assert.strictEqual(L.jobActive("working"), false);

// one build for the Building page: plan, jobs, now line, "Try next app" only while trying an app
const build = {
  id: "20261025-101010-abcdef", goal: "a recipe box", created_at: "2026-10-25T10:10:10Z", planning: false,
  now: "step 3 of at most 12: asking the model what to do next", consent: { id: "c1", said: "" }, yes: null,
  card: { lines: ["Recipes: try Mealie first (docker), then 2 more if it does not work."] },
  setup: [{ title: "Docker Desktop" }],
  knowledge: [{ job: 0, title: "USDA foods", source: "kiwix", kind: "zim", look: "https://example.org/usda", status: "offered, big download, add later (90 GB)" }, { job: 0, title: "Wikipedia (en.wikipedia.org)", source: "wikipedia", kind: "articles" }],
  cost: { usd: 0.1234, calls: 3 },
  jobs: [{ id: "20261025-101010-abcdef-0", n: 0, name: "Recipes", does: "Keep recipes", status: "building: Mealie",
           tries: ["Mealie (docker)", "Tandoor"], plan: { candidates: [{ name: "Mealie", url: "https://github.com/mealie-recipes/mealie" }] },
           run: { state: "building", trying: "Mealie", now: "running docker compose up", line: "", lines: ["a", "b"],
                  tried: [{ name: "Grocy" }], result: null } },
         { id: "20261025-101010-abcdef-1", n: 1, name: "Shopping", status: "working", plan: { reuse: "freshtable", reuse_name: "FreshTable" },
           run: { state: "working", result: { ok: true } } }],
};
const bv = L.buildView(build);
assert.strictEqual(bv.active, true);
assert.strictEqual(bv.now, build.now);
assert.deepStrictEqual(bv.planLines, build.card.lines);
assert.strictEqual(bv.consentId, "c1");
assert.deepStrictEqual(bv.setup, ["Docker Desktop"]);
assert.strictEqual(bv.knowledge[0].status, "offered, big download, add later (90 GB)");
assert.strictEqual(bv.knowledge[1].status, "");          // no status from the Core: no words made up
assert.strictEqual(bv.cost, "$0.12 over 3 cloud calls");
assert.strictEqual(bv.jobs[0].words, "Trying Mealie");
assert.strictEqual(bv.jobs[0].canNext, true);
assert.deepStrictEqual(bv.jobs[0].tried, ["Grocy"]);
assert.strictEqual(bv.jobs[1].canNext, false);
assert.strictEqual(bv.jobs[1].reuse, "FreshTable");
assert.deepStrictEqual(L.buildView({}).jobs, []);

// Home's "Building now": live builds, newest first
const now = L.buildingNow({ where: "local", builds: [
  { id: "a", goal: "old", created_at: "2026-10-01T00:00:00Z", jobs: [{ name: "x", status: "working" }] },
  { id: "b", goal: "new", created_at: "2026-10-25T00:00:00Z", jobs: [{ name: "y", status: "building: App" }] }] });
assert.deepStrictEqual(now.live.map((b) => b.id), ["b"]);
assert.strictEqual(now.live[0].jobs[0].words, "Trying App");
assert.strictEqual(now.total, 2);
assert.ok(/this computer/.test(now.where));
assert.ok(/Anthropic/.test(L.buildingNow({ where: "cloud", company: "Anthropic", builds: [] }).where));

// lane I18c: a build that stopped says so (test3: all 3 jobs held, and Home said "not building anything")
const held3 = { where: "cloud", builds: [
  { id: "old", created_at: "2026-10-01T00:00:00Z", jobs: [{ name: "x", status: "working" }] },
  { id: "space", goal: "a space bot", created_at: "2026-10-27T20:53:49+00:00",
    jobs: [{ name: "a", status: "held" }, { name: "b", status: "held" }, { name: "c", status: "held" }] }] };
assert.strictEqual(L.buildingNow(held3).live.length, 0);
const st = L.buildStopped(held3);
assert.strictEqual(st.id, "space");
assert.strictEqual(st.count, 3);
assert.strictEqual(st.line, "Your last build stopped with 3 jobs held. Open Building to see why.");
assert.strictEqual(st.head, "Your last build stopped with 3 jobs held.");
assert.strictEqual(st.here, "Your last build stopped with 3 jobs held. Each job below says why.");
assert.strictEqual(L.buildStopped({ builds: [{ id: "m", created_at: "1", jobs: [{ status: "held" }, { status: "not_yet" },
  { status: "nothing_found" }, { status: "nothing_found" }, { status: "working" }] }] }).line,
  "Your last build stopped with 1 job held, 1 job not working yet and 2 jobs with nothing found yet. Open Building to see why.");
assert.strictEqual(L.buildStopped({ builds: [{ id: "w", jobs: [{ status: "working" }] }] }), null);           // done well: nothing to say
assert.strictEqual(L.buildStopped({ builds: [{ id: "g", jobs: [{ status: "held" }, { status: "building: X" }] }] }), null);   // still going
assert.strictEqual(L.buildStopped(held3.builds[0] && { builds: [held3.builds[0]] }), null);
assert.strictEqual(L.buildStopped({ builds: [] }), null);
assert.strictEqual(L.buildStopped(null), null);
// only the newest build counts: an old stopped build under a newer good one says nothing
assert.strictEqual(L.buildStopped({ builds: [held3.builds[1], { id: "n", created_at: "2026-10-28T00:00:00Z", jobs: [{ status: "working" }] }] }), null);
// lane I18e: Home's "stopped" line links to that build's "Check again"; Building offers it only when it fits
assert.strictEqual(st.link, "#building/space");
assert.strictEqual(st.linkText, "Open Building to see why and check again.");
const againV = L.buildView({ id: "space", yes: { given: true }, jobs: [{ id: "space-0", status: "held" }, { id: "space-1", status: "working" }] });
assert.strictEqual(againV.canAgain, true);
assert.strictEqual(againV.unfinished, 1);
assert.strictEqual(againV.againAsking, "");
assert.strictEqual(L.buildView({ id: "s", yes: { given: true }, again: { state: "asking", consent_id: "c_9" },
  jobs: [{ id: "s-0", status: "not_yet" }] }).againAsking, "c_9");
assert.strictEqual(L.buildView({ id: "s", jobs: [{ id: "s-0", status: "held" }] }).canAgain, false);          // no Yes yet
assert.strictEqual(L.buildView({ id: "s", yes: { given: true }, jobs: [{ id: "s-0", status: "held" }, { id: "s-1", status: "building: X" }] }).canAgain, false);
assert.strictEqual(L.buildView({ id: "s", yes: { given: true }, jobs: [{ id: "s-0", status: "working" }] }).canAgain, false);

// what's running, from the Registry
const sum = L.runningSummary(rows.concat([{ id: "b", name: "Broken", status: "broken" }, { id: "n", name: "N", status: "needs_you" },
                                          { id: "p", name: "Page", status: "stopped", process: false }]));
assert.strictEqual(sum.running, 6);
assert.deepStrictEqual(sum.down, ["World Monitor"]);
assert.strictEqual(sum.broken, 1);
assert.strictEqual(sum.needs, 1);
assert.strictEqual(sum.ready, 1);

// the brain in one line
assert.strictEqual(L.brainView({ settings: { model: "qwen3:8b" }, state: { state: "ready" } }).ok, true);
assert.strictEqual(L.brainView({ settings: { model: "qwen3:8b" }, state: { state: "loading" } }).words, "Loading");
const none = L.brainView({ settings: { model: "" }, state: { state: "error", reason: "no brain is picked yet" } });
assert.strictEqual(none.name, "No brain picked yet");
assert.strictEqual(none.reason, "");                          // said once, not twice
assert.strictEqual(none.words, "Pick one in Setup");
assert.strictEqual(none.ok, false);

// the outside log in plain words: who, which host, allowed or refused, why
const who = { freshtable: "FreshTable" };
const done = L.ledgerLine({ t: "2026-10-25T10:00:00Z", subject: "pkg:freshtable", host: "api.example.org", outcome: "done", code: "granted" }, who);
assert.strictEqual(done.text, "FreshTable reached api.example.org: you said Yes to it.");
assert.strictEqual(done.allowed, true);
const refused = L.ledgerLine({ subject: "toolsmith:search", host: "github.com", outcome: "refused", code: "bridge_closed" }, who);
assert.strictEqual(refused.text, "Toolsmith, searching for apps was refused github.com: the Bridge was closed.");
assert.strictEqual(refused.allowed, false);
assert.strictEqual(L.ledgerLine({ subject: "agent:concierge", host: "", outcome: "failed", code: "no_answer" }).text,
                   "Concierge was allowed to reach a host it did not name, but it did not work: nothing answered.");
assert.strictEqual(L.ledgerLine({ event: "chain_rotated" }).allowed, null);
assert.strictEqual(L.whoOf("core:update"), "BuildBot (update)");
assert.strictEqual(L.whoOf("weird"), "Something unknown");

// audit lines; Drawbridge's own, newest first
const audit = [{ t: "1", actor: "drawbridge", action: "check_package", package: "freshtable", verdict: "allow" },
               { t: "2", actor: "core", action: "start", package: "x" },
               { t: "3", actor: "drawbridge", action: "review", package: "freshtable", tool: "add_item", verdict: "ask", code: "needs_yes" }];
assert.strictEqual(L.auditLine(audit[0]).text, "Drawbridge checked freshtable: allow.");
assert.strictEqual(L.auditLine(audit[2]).text, "Drawbridge reviewed freshtable.add_item: ask (needs_yes).");
assert.strictEqual(L.auditLine(audit[1]).drawbridge, false);
assert.deepStrictEqual(L.drawbridgeChecks(audit, 5).map((l) => l.when), ["3", "1"]);

// Home layout (v1 arrange): pinned first, her order, new tiles after; hidden go to the tray; her names win
const tiles = [{ id: "running", name: "What's running" }, { id: "building", name: "Building now" },
               { id: "bridge", name: "The Bridge" }, { id: "freshtable:list", name: "Shopping list" }];
const placed = L.arrangeTiles(tiles, { tiles: { order: ["bridge", "running"], pinned: ["freshtable:list"], hidden: ["building"],
                                                names: { bridge: "Outside" } } });
assert.deepStrictEqual(placed.visible.map((t) => t.id), ["freshtable:list", "bridge", "running"]);
assert.strictEqual(placed.visible[0].pinned, true);
assert.strictEqual(placed.visible[1].name, "Outside");
assert.deepStrictEqual(placed.hidden.map((t) => t.id), ["building"]);
assert.deepStrictEqual(L.arrangeTiles(tiles, {}).visible.map((t) => t.id), tiles.map((t) => t.id));
assert.strictEqual(L.movePosition(["a", "b", "c"], "b", -1), 1);
assert.strictEqual(L.movePosition(["a", "b", "c"], "b", 1), 3);
assert.strictEqual(L.movePosition(["a", "b", "c"], "a", -1), null);
assert.deepStrictEqual(L.seenTiles(tiles.slice(0, 1)), { running: "What's running" });

// Apps > Add: GitHub goes to Toolsmith (that project only), a folder to the Package Manager, words to Toolsmith
const gh = L.addSource(" https://github.com/mealie-recipes/mealie.git ");
assert.strictEqual(gh.kind, "github");
assert.deepStrictEqual(gh.plan.jobs[0].pasted, ["https://github.com/mealie-recipes/mealie"]);
assert.strictEqual(gh.plan.jobs[0].name, "mealie");
assert.strictEqual(L.addSource("C:\\Apps\\mine").kind, "folder");
assert.strictEqual(L.addSource("\\\\server\\share\\app").kind, "folder");
assert.deepStrictEqual(L.addSource("freshtable"), { kind: "builtin", source: "freshtable" });
assert.deepStrictEqual(L.addSource("a recipe box for my family"), { kind: "describe", plan: { goal: "a recipe box for my family" } });
assert.strictEqual(L.addSource("   ").kind, "empty");
assert.strictEqual(L.addSource("https://gitlab.com/a/b").kind, "describe");     // not GitHub: Toolsmith reads the words

// available apps: built-ins not installed, less the Desktop and the wizard
assert.deepStrictEqual(L.availableApps([{ id: "freshtable", name: "FreshTable", kind: "app", description: "d" },
  { id: "desktop", kind: "ui" }, { id: "wizard", kind: "wizard" }, { id: "example-page", kind: "page" }]).map((a) => a.id), ["freshtable"]);
assert.strictEqual(L.storePrompt({ wizard_complete: true }, {}, [{ id: "example-page", name: "My notes", kind: "page" }]).show, false);

// an installed app's buttons
assert.deepStrictEqual(L.appActions({ kind: "app", status: "stopped" }).map((a) => a.id), ["start", "update", "logs", "remove"]);
assert.deepStrictEqual(L.appActions({ kind: "app", status: "running" }).map((a) => a.id), ["stop", "update", "logs", "remove"]);
assert.deepStrictEqual(L.appActions({ kind: "page", status: "stopped", process: false }).map((a) => a.id), ["update", "logs", "remove"]);
assert.deepStrictEqual(L.appActions({ kind: "avatar", status: "stopped" }).map((a) => a.id), ["update", "logs", "remove"]);
// lane I16: "Update" only when an update's Yes card waits; otherwise it checks
assert.strictEqual(L.appActions({ kind: "app", status: "running" }).find((a) => a.id === "update").label, "Check for an update");
assert.deepStrictEqual(L.appActions({ kind: "app", status: "running" }, true).find((a) => a.id === "update"), { id: "update", label: "Update", icon: "update", waiting: true });

// the chat stream: whole NDJSON lines only; one turn from its events
const nd = L.ndjson('{"type":"token","text":"Hel"}\n{"type":"token","text":"lo"}\n{"type":"to');
assert.strictEqual(nd.events.length, 2);
assert.strictEqual(nd.rest, '{"type":"to');
assert.strictEqual(L.ndjson("not json\n").events[0].type, "error");
const turn = new L.ChatTurn();
turn.add({ type: "progress", tool: "freshtable.add_item", line: "Using freshtable.add_item ..." });
assert.strictEqual(turn.talking(), false);                     // no words yet: the mouth stays shut
turn.add({ type: "ask", question: { consent_id: "c9", card: { title: "May FreshTable add milk?" } } });
turn.add({ type: "answered", yes: true });
turn.add({ type: "tool_done", tool: "freshtable.add_item", ok: true });
turn.add({ type: "token", text: "Added " });
assert.strictEqual(turn.talking(), true);
turn.add({ type: "open", urls: ["http://freshtable.localhost:8422/", "javascript:alert(1)"] });
turn.add({ type: "done", text: "Added milk.", changed: [{ tool: "freshtable.add_item" }], notice: null });
assert.strictEqual(turn.text, "Added milk.");
assert.strictEqual(turn.talking(), false);
assert.deepStrictEqual(turn.steps.map((s) => s.state), ["done"]);
assert.deepStrictEqual(turn.asks, [{ id: "c9", title: "May FreshTable add milk?", answered: true }]);
assert.deepStrictEqual(turn.opens, ["http://freshtable.localhost:8422/"]);
assert.deepStrictEqual(turn.changed, ["freshtable.add_item"]);
const bad = new L.ChatTurn();
bad.add({ type: "error", error: "The brain is loading." });
assert.strictEqual(bad.done, true);
assert.strictEqual(bad.error, "The brain is loading.");
assert.strictEqual(L.stepWords("failed"), "Did not work");

// the Developer terminal (lane I8c's events)
const sh = new L.ShellLog(3);
sh.add({ t: 1, stream: "stdout", text: "a\n" });
sh.add({ t: 2, stream: "stderr", text: "b\n" });
sh.add({ t: 3, stream: "odd", text: "c\n" });
sh.add({ t: 4, stream: "stdout", text: "d\n" });
assert.deepStrictEqual(sh.lines.map((l) => l.stream + l.text), ["stderrb\n", "stdoutc\n", "stdoutd\n"]);
sh.add({ exit: 0 });
assert.strictEqual(sh.exit, 0);
assert.strictEqual(L.SHELL_NOTE, "This is a full command line on your computer. Commands here are not checked by the Bridge.");

// the seven agents, with live lines where a route gives one
assert.deepStrictEqual(L.AGENTS.map((a) => a.name), ["Archivist", "Concierge", "Steward", "Caretaker", "Majordomo", "Drawbridge", "Toolsmith"]);
const agents = L.agentRows({ running: sum, vault: { state: "locked" }, steps: 2, building: now,
                             checks: L.drawbridgeChecks(audit, 5), safe: [] });
assert.strictEqual(agents.find((a) => a.id === "archivist").now, "Your vault is locked.");
assert.strictEqual(agents.find((a) => a.id === "majordomo").now, "2 things on Your turn.");
assert.strictEqual(agents.find((a) => a.id === "toolsmith").now, "1 build going now.");
assert.strictEqual(agents.find((a) => a.id === "drawbridge").now, "Drawbridge reviewed freshtable.add_item: ask (needs_yes).");
assert.strictEqual(agents.find((a) => a.id === "caretaker").now, "1 not working.");
assert.ok(/^Safe mode: /.test(L.agentRows({ running: sum, safe: ["A check failed."] }).find((a) => a.id === "caretaker").now));
assert.strictEqual(L.agentRows({}).find((a) => a.id === "concierge").now, "");
assert.ok(L.AGENTS.every((a) => a.zone && a.does && a.built));

// the vault: states in words, never a value
const vv = L.vaultView({ state: "open", ways_in: ["PIN"], items: 3, inbox_waiting: 1, inbox_conflicts: ["ANTHROPIC_API_KEY"],
                         idle_lock_minutes: 15, store: { location: "C:/bot/vault" } });
assert.strictEqual(vv.words, "Open");
assert.strictEqual(vv.open, true);
assert.deepStrictEqual(vv.conflicts, ["ANTHROPIC_API_KEY"]);
assert.strictEqual(vv.idle, "Locks itself after 15 minutes with no use.");
assert.strictEqual(L.vaultView({ state: "not_set_up" }).setUp, false);
assert.strictEqual(L.vaultView({}).words, "Not checked");

// a person's id from her name
assert.strictEqual(L.personId("Sam Lee", []), "sam-lee");
assert.strictEqual(L.personId("Sam", ["sam"]), "sam-2");
assert.strictEqual(L.personId("Zo\u00eb", []), "zoe");
assert.strictEqual(L.personId("", []), "person");
assert.ok(/^[a-z0-9][a-z0-9_-]{0,39}$/.test(L.personId("-- !! --", [])));

// zone provider slots
const slots = L.slotRows([{ slot: "zone2.model-runtime", how_many: "one", installed: ["ollama-x"], active: "ollama-x" },
                          { slot: "zone1.detector", how_many: "many", installed: ["uk-nhs"], active: ["uk-nhs"] },
                          { slot: "zone1.vault-store", how_many: "one", installed: ["usb"], active: null }]);
assert.deepStrictEqual(slots.map((s) => s.canPick), [true, false, false]);
assert.deepStrictEqual(slots[1].active, ["uk-nhs"]);

// Puff's mouth (packages/cloud-avatar/face.html): on the owner's drawn smile, v1's attack and decay
const fs = require("fs");
const vm = require("vm");
const html = fs.readFileSync(path.join(__dirname, "..", "..", "packages", "cloud-avatar", "face.html"), "utf8");
const src = /<script id="puff-mouth">([\s\S]*?)<\/script>/.exec(html)[1];
const box = { module: { exports: {} } };
vm.runInNewContext(src, box);
const M = box.module.exports;
// at rest the lens lies on her smile (corners 860 and 961 at y 830, control 910,896) and has no area
assert.strictEqual(M.mouthPath(0), "M860 830 Q910 896.00 961 830 Q910 896.00 860 830 Z");
assert.strictEqual(M.mouthPath(1), "M860 830 Q910 866.00 961 830 Q910 981.00 860 830 Z");
assert.ok(html.indexOf('d="' + M.mouthPath(0) + '"') > 0);
assert.ok(/id="mouth"[^>]*visibility="hidden"/.test(html));           // hidden at rest: her drawn smile shows
assert.strictEqual(M.shown(0), false);
assert.strictEqual(M.shown(0.01), false);
assert.strictEqual(M.shown(0.3), true);
assert.strictEqual(M.nextOpen(0, 1), 0.5);                     // fast attack
assert.ok(Math.abs(M.nextOpen(0.5, 0) - 0.425) < 1e-9);         // slow decay
assert.strictEqual(M.pulseTarget(0), 0);
assert.ok(M.pulseTarget(5) > 0.35 && M.pulseTarget(5) <= 1);
// her own picture, as is, and her colors (ink and the deep purple of her eyes, sampled from it)
assert.ok(html.indexOf('<image href="puff.webp" x="0" y="0" width="2000" height="2000"/>') > 0);
assert.ok(html.indexOf('viewBox="0 424 2000 1150"') > 0);
["#000000", "#500078"].forEach((c) => assert.ok(html.indexOf(c) > 0, c));
["#221226", "#d883e6", "M236 138"].forEach((c) => assert.ok(html.indexOf(c) < 0, c));   // not v1's drawn mascot

// the face: her package's face page on its own origin, its picture, else the Desktop's own copy
const avatarRow = { id: "cloud-avatar", kind: "avatar", name: "Puff", own: true, origin: "core", status: "stopped", process: false,
                    ui: { pages: [{ id: "face", title: "Puff", file: "face.html", show: "in_desktop" }] } };
const av = { id: "cloud-avatar", name: "Puff", preview: "/pkg/cloud-avatar/preview.png" };
assert.deepStrictEqual(L.faceFor(av, [avatarRow], PAGES),
  { kind: "face", src: "http://cloud-avatar.localhost:8422/face.html", name: "Puff", origin: "http://cloud-avatar.localhost:8422" });
assert.deepStrictEqual(L.faceFor(av, [Object.assign({}, avatarRow, { ui: {} })], PAGES),
  { kind: "picture", src: "http://cloud-avatar.localhost:8422/preview.png", name: "Puff", origin: null });
assert.strictEqual(L.faceFor(av, [], PAGES).kind, "own");                  // before setup: not installed yet
assert.strictEqual(L.faceFor(av, [], PAGES).src, "/ui/img/puff.webp");
assert.strictEqual(L.faceFor(av, [Object.assign({}, avatarRow, { status: "removed" })], PAGES).kind, "own");
assert.strictEqual(L.faceFor(av, [avatarRow], "").kind, "own");           // no pages address yet
assert.strictEqual(L.faceFor(null, [], PAGES).src, L.OWN_FACE);
assert.strictEqual(L.FACE_SANDBOX, "allow-scripts allow-same-origin");

// the Developer command line's answers (lane I8c) in plain words
assert.strictEqual(L.shellSaid(409, { code: "safe_mode" }), "BuildBot is in safe mode, so the command line is closed.");
assert.strictEqual(L.shellSaid(429, { code: "too_many" }), "Two command lines are already open. Close one first.");
assert.strictEqual(L.shellSaid(404, null), "That command line has closed.");
assert.strictEqual(L.shellSaid(503, { error: "PowerShell did not start." }), "PowerShell did not start.");
assert.strictEqual(L.shellSaid(400, {}), "Send one line at a time, with no line breaks.");
assert.strictEqual(L.ctrlcSaid("restarted"), "The command line was busy, so BuildBot started a fresh one.");
assert.strictEqual(L.ctrlcSaid("stopped"), "Stopped.");
assert.strictEqual(L.ctrlcSaid("idle"), "Nothing was running.");
assert.strictEqual(L.shellLine("dir"), "dir");
assert.strictEqual(L.shellLine("a\nb"), null);
assert.strictEqual(L.shellLine("a\u0000"), null);
assert.strictEqual(L.shellLine("x".repeat(8001)), null);
assert.strictEqual(L.shellLine("x".repeat(8000)).length, 8000);
// lane I8c's error stream is "err": shown as error output
const errLog = new L.ShellLog();
errLog.add({ t: 1, stream: "err", text: "boom\n" });
errLog.add({ t: 2, stream: "out", text: "fine\n" });
assert.deepStrictEqual(errLog.lines.map((l) => l.stream), ["stderr", "stdout"]);
// the status line: a folder only when the route gave one; after a reload, only the shell's name
assert.deepStrictEqual(L.shellState({ id: "s1", cwd: "C:\\Users\\sam", shell: "powershell" }),
                       { text: "Running in C:\\Users\\sam", input: true, buttons: true });
assert.deepStrictEqual(L.shellState({ id: "s1", cwd: "", shell: "powershell" }), { text: "PowerShell is open.", input: true, buttons: true });
assert.strictEqual(L.shellState({ id: "s1", shell: "cmd" }).text, "Command Prompt is open.");
assert.strictEqual(L.shellState({ id: "s1", shell: "powershell" }).text.indexOf("Running in"), -1);
// no input while Ctrl+C is on its way; none after the route said the shell is gone
assert.deepStrictEqual(L.shellState({ id: "s1", shell: "cmd", busy: true }), { text: "Command Prompt is open. Stopping what runs...", input: false, buttons: true });
assert.deepStrictEqual(L.shellState({ id: "s1", closed: true }), { text: "This command line is closed.", input: false, buttons: false });
assert.strictEqual(L.shellState({ id: "s1", exited: true }).input, false);
assert.deepStrictEqual(L.shellState({}), { text: "No terminal open.", input: false, buttons: false });

require("./i16.test.js")(L);
require("./i17.test.js")(L);
console.log("ok");

// D121: a free site is a job's answer with nothing installed: its own words, and never Working or done.
assert.strictEqual(L.jobWords("free_site"), "A free site does this, nothing installed");
assert.strictEqual(L.jobActive("free_site"), false);
