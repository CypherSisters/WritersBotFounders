// The Desktop's own tick logic (logic.js consentCard) for one Yes card from GET /v1/consent.
// stdin: {"record": {...}, "ticked": [...]}; stdout: {"kind", "route", "body", "values", "note"} or null.
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const got = JSON.parse(raw);
  const t = L.consentCard(got.record).ticks;
  process.stdout.write(JSON.stringify(t ? { kind: t.kind, route: t.route, body: t.body(got.ticked || []),
    values: t.items.map(function (i) { return i.value; }), note: t.note } : null));
});
