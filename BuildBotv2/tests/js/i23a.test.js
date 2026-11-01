// Lane i23a (D95): the Developer page's setting for files Drawbridge can't read, and its audit line. Prints "ok".
const assert = require("assert");
const path = require("path");
const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");
const D = require(path.join(web, "devzones.js"));
const L = require(path.join(web, "logic.js"));

const got = { files: "normal", title: "Files Drawbridge can't read",
              choices: [{ id: "normal", name: "Normal", line: "N", on: true }, { id: "locked", name: "Locked down", line: "L", on: false },
                        { id: "open", name: "Open", line: "never shown" }] };
const v = D.filesChoices(got);
assert.strictEqual(v.title, "Files Drawbridge can't read");
assert.strictEqual(v.now, "normal");
assert.deepStrictEqual(v.choices.map((c) => [c.id, c.name, c.on]), [["normal", "Normal", true], ["locked", "Locked down", false]]);
assert.deepStrictEqual(D.filesChoices({ files: "locked", choices: got.choices }).choices.map((c) => c.on), [false, true]);
assert.strictEqual(D.filesChoices({ files: "weird" }).now, "");
assert.strictEqual(D.filesChoices(null).choices.length, 0);

// her change in the audit log, in plain words, with Drawbridge's checks
const line = L.auditLine({ t: "2026-10-30T10:00:00Z", actor: "owner", action: "drawbridge_files", from: "normal", to: "locked" });
assert.strictEqual(line.drawbridge, true);
assert.strictEqual(line.text, "You set files Drawbridge can't read to Locked down.");
assert.strictEqual(L.auditLine({ actor: "owner", action: "drawbridge_files", to: "normal" }).text, "You set files Drawbridge can't read to Normal.");
console.log("ok");
