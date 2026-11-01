// The PuffPAI window (D81): its rules with no DOM. Run by tests/test_window_page.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
const BASE = "http://127.0.0.1:8422";

const rows = [
  { id: "builtapp", name: "Built App", kind: "app", status: "running", port: 51000, origin: "made", health: { ok: true }, description: "Lists and groceries",
    ui: { pages: [{ id: "main", path: "/", show: "in_desktop" }] } },
  { id: "outside", name: "Outside One", kind: "app", status: "running", port: 51001, origin: "adopted:https://github.com/x/y", health: { ok: false },
    ui: { pages: [{ id: "main", url: "http://127.0.0.1:{port}/", show: "new_tab" }] } },
  { id: "plain-tool", name: "Plain Tool", kind: "tool", status: "running", origin: "made", health: { ok: true }, ui: {} },
  { id: "paint", name: "Paint", kind: "app", status: "stopped", ui: { open: { program: "paint.exe" } } },
  { id: "gone", name: "Gone", kind: "app", status: "removed", ui: {} },
  { id: "desktop", name: "Desktop", kind: "ui", status: "running" },
  { id: "look-x", name: "A look", kind: "look", status: "running" },
  { id: "face", name: "A face", kind: "avatar", status: "running" },
  { id: "slot", name: "A provider", kind: "provider", status: "running" },
];

// -- the rail: every app and tool, a tool with no page too; no rule here about where it opens
const apps = L.windowApps(rows);
assert.deepStrictEqual(apps.map((a) => a.pkg), ["builtapp", "outside", "plain-tool", "paint"]);
assert.strictEqual(apps[0].firstPage, "main");
assert.strictEqual(apps[1].down, true);
assert.strictEqual(apps[3].program, true);
assert.strictEqual(Object.keys(apps[0]).some((k) => /target|kind|url/.test(k)), false);

// -- the / list: any word finds a command, by what it does
const names = (q, list) => L.commandHits(q, list || L.WINDOW_COMMANDS).map((c) => c.name);
assert.strictEqual(names("/").length, L.WINDOW_COMMANDS.length);
assert.deepStrictEqual(names("/password"), ["/vault", "/unlock", "/lock"]);
assert.ok(names("/api").indexOf("/keys") >= 0);
assert.ok(names("/cost cap").indexOf("/keys") >= 0);
assert.deepStrictEqual(names("/nosuchword"), []);
const withApps = L.WINDOW_COMMANDS.concat(L.appCommands(apps, rows));
assert.strictEqual(L.commandHits("/groceries", withApps)[0].args, "Built App");
assert.strictEqual(L.commandHits("/groceries", withApps)[0].from, "Built App");
assert.deepStrictEqual(L.commandParse("/Build a recipe box  "), { name: "/build", rest: "a recipe box" });
assert.deepStrictEqual(L.commandParse("/plan"), { name: "/plan", rest: "" });
assert.strictEqual(L.commandParse("plain words"), null);

// -- the brain's words, as blocks of plain text (nothing that could be HTML is ever made here)
const md = L.mdBlocks("## Head\n\nSome **bold**, *soft* and `code`.\nSame para.\n\n- one\n- [two](https://example.com/a)\n\n1. first\n\n```\nx = 1\n```\n<script>no()</script>");
assert.deepStrictEqual(md.map((b) => b.type), ["h", "p", "ul", "ol", "code", "p"]);
assert.deepStrictEqual(md[1].inline.map((i) => i.t), ["text", "b", "text", "i", "text", "code", "text"]);
assert.strictEqual(md[2].items[1][0].url, "https://example.com/a");
assert.strictEqual(md[5].inline[0].text, "<script>no()</script>");
assert.deepStrictEqual(L.mdInline("[bad](javascript:alert(1))").map((i) => i.t), ["text", "text"]);
assert.strictEqual(L.mdInline("see http://127.0.0.1:5/x.")[1].url, "http://127.0.0.1:5/x");
assert.strictEqual(L.mdInline("see http://127.0.0.1:5/x.")[2].text, ".");
assert.deepStrictEqual(L.mdBlocks("```\nstill streaming").map((b) => b.type), ["code"]);

// -- only a plain web address may open outside
assert.strictEqual(L.webUrl("http://127.0.0.1:5/"), true);
assert.strictEqual(L.webUrl("https://example.com"), true);
["javascript:alert(1)", "ms-settings:", "file:///c:/x", "http://me:pw@example.com/", "", null].forEach((u) => assert.strictEqual(L.webUrl(u), false, String(u)));

// -- the open event's address, back to its row
const id = (u) => { const r = L.openMatch(u, rows, BASE); return r && r.id; };
assert.strictEqual(id("http://builtapp.localhost:8422/x"), "builtapp");
assert.strictEqual(id("http://127.0.0.1:51000/"), "builtapp");
assert.strictEqual(id("http://127.0.0.1:51001/news"), "outside");
assert.strictEqual(id("http://builtapp.localhost:9999/"), null);
assert.strictEqual(id("http://127.0.0.1:1/"), null);
assert.strictEqual(id("https://example.com/"), null);
assert.strictEqual(id("javascript:alert(1)"), null);

// -- "Show <app>" chips: what the turn named, or used
const pick = (t, tools) => L.appsNamed(t, tools, apps).map((a) => a.pkg);
assert.deepStrictEqual(pick("Paint and then Built App, yes.", []), ["paint", "builtapp"]);
assert.deepStrictEqual(pick("nothing here", ["plain-tool.say"]), ["plain-tool"]);
assert.deepStrictEqual(pick("painter is another word", []), []);

// -- /show
assert.strictEqual(L.showTarget("me my building", apps).kind, "building");
assert.strictEqual(L.showTarget("your turn", apps).kind, "turn");
assert.strictEqual(L.showTarget("the store", apps).kind, "store");
assert.strictEqual(L.showTarget("me the PuffPAI store", apps).kind, "store");
assert.ok(L.WINDOW_COMMANDS.some((c) => c.name === "/store"));
assert.strictEqual(L.showTarget("built", apps).app.pkg, "builtapp");
assert.strictEqual(L.showTarget("the Outside One", apps).app.pkg, "outside");
assert.strictEqual(L.showTarget("nothing like it", apps).kind, "none");
assert.strictEqual(L.showTarget("p", apps).kind, "many");

// -- a build's chip
const chip = (b) => L.buildChip(b).words;
assert.strictEqual(L.buildChip({ jobs: [{ status: "planned" }] }, true).words, "waiting on you");
assert.strictEqual(chip({ jobs: [{ status: "planned" }] }), "starting");
assert.strictEqual(chip({ jobs: [{ status: "waiting_you" }] }), "waiting on you");
assert.strictEqual(chip({ yes: true, jobs: [{ status: "building: an app" }] }), "building");
assert.strictEqual(chip({ yes: true, jobs: [{ status: "working" }, { status: "held" }] }), "stopped");
assert.strictEqual(chip({ yes: true, jobs: [{ status: "working" }] }), "done");
assert.strictEqual(chip({ jobs: [{ status: "said_no" }] }), "no Yes");

// -- the chats: main first, whatever was saved; only ids the Core takes
const chats = L.chatList([{ id: "c-1", title: "One" }, { id: "main", title: "Mine" }, { id: "../bad" }, { id: "c-1" }, "x", { id: "c-2" }]);
assert.deepStrictEqual(chats, [{ id: "main", title: "Mine" }, { id: "c-1", title: "One" }, { id: "c-2", title: "New chat" }]);
assert.strictEqual(L.chatList(null)[0].id, "main");
assert.strictEqual(L.chatTitle("first line\nsecond"), "first line");
assert.ok(L.chatTitle("x".repeat(90)).length <= 42);
assert.ok(/^c-[A-Za-z0-9]{1,16}$/.test(L.newChatId()));

// -- sign-in and cards
assert.strictEqual(L.pairCode("#pair=AB-12"), "AB-12");
assert.strictEqual(L.pairCode("#building"), "");
assert.strictEqual(L.cardShort("c_abcdef123"), "abcdef");
assert.strictEqual(L.cardShort("abcdef123"), "abcdef");
console.log("ok");
