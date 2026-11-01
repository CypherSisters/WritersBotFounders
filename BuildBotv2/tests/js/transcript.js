// Feeds recorded wizard events through the Desktop's own logic.js and prints what the page shows.
// stdin: [{"screen": {...}} | {"typed": {"field": {...}, "value": ...}}]; stdout: ["visible text", ...]
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const t = new L.WizardTranscript();
  for (const e of JSON.parse(raw)) {
    if (e.screen) t.push(e.screen);
    else t.typed(e.typed.field, e.typed.value);
  }
  process.stdout.write(JSON.stringify(t.visibleText()));
});
