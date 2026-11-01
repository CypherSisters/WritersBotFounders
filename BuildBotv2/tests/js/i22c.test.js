// Lane I22c (D92): the signed-out Desktop's one sign-in screen and Settings' plain "not built yet" lines.
// Run by tests/test_i22c_sign_in.py.
const assert = require("assert");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const NEVER = /bearer|token|api key|authorization|\/v1\/|coming in a later build|not in this build/i;

// the file and its full folder, for any install folder (a recipe bot, an invoices bot, a birdwatching bot)
[["C:\\PuffPAI-recipes\\BuildBotv2", "recipes"], ["D:\\Shop invoices\\BuildBotv2", "invoices"],
 ["C:\\Users\\someone\\Birds\\BuildBotv2", "birds"]].forEach(([folder]) => {
  const w = L.signInWords({ file: "Sign in a browser.cmd", folder: folder });
  assert.strictEqual(w.title, "Sign in this browser");
  assert.strictEqual(w.line, "To sign in this browser, double-click Sign in a browser.cmd in " + folder +
    ", then open the link it shows.");
  assert.ok(!NEVER.test(w.title + " " + w.line), w.line);
  const other = L.signInWords({ file: "Sign in a browser.cmd", folder: folder }, "", true).line;
  assert.strictEqual(other, "To sign in another browser on this computer, double-click Sign in a browser.cmd in " +
    folder + ", then open the link it shows in that browser.");
});
// no file (run from source), no answer, or junk: still plain words, never the Core's refusal text
[null, undefined, {}, { file: "", folder: "" }, { file: 3, folder: ["x"] }, "Bearer abc"].forEach((w) => {
  const got = L.signInWords(w).line;
  assert.strictEqual(got, "To sign in this browser, double-click Sign in a browser.cmd in the folder BuildBot is installed in, then open the link it shows.");
  assert.ok(!NEVER.test(got), got);
});
// run from source: the one command that really runs here, never bare "puffpai"
const cmd = '& "C:\\X\\.venv\\Scripts\\puffpai.exe" pair --running';
assert.strictEqual(L.signInWords({ file: "", folder: "", command: cmd }).line,
  "To sign in this browser, run this in a terminal on this computer, then open the link it shows: " + cmd);
// chatting in a terminal: the launcher where there is one, else the command, never bare "puffpai"
assert.ok(/double-click Chat with BuildBot\.cmd in C:\\X\./.test(L.chatWords({ chat_file: "Chat with BuildBot.cmd", chat_folder: "C:\\X" })));
assert.ok(L.chatWords({ chat_command: "& x.exe" }).indexOf("run this there: & x.exe") > 0);
[null, {}].forEach((w) => assert.ok(!/run puffpai/.test(L.chatWords(w)) && !/PowerShell/.test(L.chatWords(w))));
// a used or old link says so first
const expired = L.signInWords({ file: "Sign in a browser.cmd", folder: "C:\\X" }, "expired").line;
assert.ok(expired.startsWith("That sign-in link was already used, or is older than 10 minutes. To sign in this browser,"), expired);
assert.ok(!NEVER.test(expired));

// only the Core's sign-in refusal switches to the sign-in screen: a 401 with no code, or code "sign_in"
assert.strictEqual(L.signedOut(401), true);
assert.strictEqual(L.signedOut(401, { error: "Sign in first." }), true);
assert.strictEqual(L.signedOut(401, { error: "Sign in first.", code: "sign_in" }), true);
assert.strictEqual(L.signedOut(401, "plain text"), true);
// a route's own 401 keeps its words: a wrong vault PIN (code "wrong") never signs the Desktop out
assert.strictEqual(L.signedOut(401, { error: "That didn't match.", code: "wrong" }), false);
assert.strictEqual(L.signedOut(401, { error: "x", code: "anything_else" }), false);
[200, 403, 404, 423, 500, 0].forEach((s) => assert.strictEqual(L.signedOut(s, {}), false, String(s)));

// Settings' "not built yet" lines: plain, one line each, no route names or developer words
Object.values(L.NOT_BUILT).forEach((line) => {
  assert.ok(!NEVER.test(line), line);
  assert.ok(!/\u2014/.test(line), line);
  assert.ok(/not built yet/.test(line), line);
});
assert.strictEqual(L.NOT_BUILT.brain, "Changing the brain here is not built yet. Setup's brain step changes it.");

console.log("ok");
