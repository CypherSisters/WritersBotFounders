// Lane looks (D76): the Desktop's side of looks and fewer tiles. Run by tests/test_looks.py.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
const PACKAGES = path.join(__dirname, "..", "..", "packages");

// every look in the store passes the Desktop's own check whole: nothing is dropped on the way to :root
// (Clean is built in since D90: tests/test_looks.py checks the built-in looks the same way)
["look-contrast", "look-paper", "look-slate"].forEach((id) => {
  const m = JSON.parse(fs.readFileSync(path.join(PACKAGES, id, "puffpai.json"), "utf8"));
  const list = L.themeList([Object.assign({ id: id, name: m.name }, m.look)]);
  assert.strictEqual(list[0].id, id);
  assert.deepStrictEqual(Object.keys(list[0].tokens).sort(), Object.keys(m.look.tokens).sort(), id);
  assert.deepStrictEqual(Object.keys(list[0].dayTokens).sort(), Object.keys(m.look.day_tokens).sort(), id);
  assert.strictEqual(L.swatchParts(list[0]).length, 2);                 // night and day previews
  assert.strictEqual(L.themeTokensFor(list[0], "day")["--bg"], m.look.day_tokens["--bg"]);
  assert.strictEqual(L.themeTokensFor(list[0], "night")["--bg"], m.look.tokens["--bg"]);
});

// the same refusals as the Core (puffpai_core/looks.py)
const bad = ["url(x)", "image(\"x.png\")", "image-set(\"a\" 1x)", "src(\"x\")", "@import x", "red; color: blue",
             "a}b", "<b>", "a\\75 rl(x)", "cross-fade(a, b)", "attr(title)", "element(#x)", "x".repeat(121), ""];
bad.forEach((v) => assert.deepStrictEqual(L.themeTokens({ tokens: { "--bg": v } }), {}, v));
const good = { "--bg": "#0F1115", "--round": 0, "--accent": "linear-gradient(140deg, #fff, rgba(0, 0, 0, 0.5))",
               "--display": "\"JetBrains Mono\", ui-monospace, monospace", "--gap": "clamp(8px, 1vw, 12px)" };
assert.deepStrictEqual(Object.keys(L.themeTokens({ tokens: good })).sort(), Object.keys(good).sort());
assert.deepStrictEqual(L.themeTokens({ tokens: { "bg": "#fff", "--x y": "#fff" } }), {});
assert.deepStrictEqual(L.LOOK_FUNCTIONS.slice().sort(),
  ["calc", "clamp", "color-mix", "hsl", "hsla", "linear-gradient", "max", "min", "radial-gradient", "rgb", "rgba", "var"]);

// a look is not a tray item and has no Start or Stop
assert.deepStrictEqual(L.trayItems([{ id: "look-clean", kind: "look", name: "Clean", status: "stopped", ui: { pages: [{ id: "p", path: "/" }] } }], "http://127.0.0.1:8422"), []);
assert.deepStrictEqual(L.appActions({ id: "look-clean", kind: "look", status: "stopped" }).map((a) => a.id), ["update", "logs", "remove"]);

// the store: one panel with sections, Apps, Looks, Avatars, Tools, Skills, then the rest (D96)
const secs = L.storeSections([{ id: "b", kind: "avatar" }, { id: "x", kind: "bundle" }, { id: "a", kind: "look" }, { id: "c", kind: "look" }]);
assert.deepStrictEqual(secs.map((s) => s.title), ["Looks", "Avatars", "Other parts"]);
assert.deepStrictEqual(secs[0].items.map((p) => p.id), ["a", "c"]);
assert.deepStrictEqual(L.storeSections([{ id: "x", kind: "skill" }]).map((s) => s.title), ["Skills"]);
assert.deepStrictEqual(L.storeSections(null), []);

// repeated notices fold into the newest one, with a count; Show all opens that title only
const titles = ["Your plan", "Your plan", "Sign this browser in", "Your plan", "Your plan", "Your plan"];
const folds = L.noticeFolds(titles);
assert.deepStrictEqual(folds.map((f) => f.hidden), [false, true, false, true, true, true]);
assert.strictEqual(folds[0].lead, true);
assert.strictEqual(folds[0].more, 4);
assert.strictEqual(folds[2].more, 0);
const open = L.noticeFolds(titles, new Set(["Your plan"]));
assert.deepStrictEqual(open.map((f) => f.hidden), [false, false, false, false, false, false]);
assert.deepStrictEqual(L.noticeFolds([]), []);
assert.deepStrictEqual(L.foldWords(4, false), { count: "+4 MORE", button: "Show all 5" });
assert.strictEqual(L.foldWords(4, true).button, "Hide the others");

console.log("ok");
