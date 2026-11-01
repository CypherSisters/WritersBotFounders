// The Desktop's model of Yes cards (logic.consentCard), for cards the Core really served
// (GET /v1/consent). Reads a JSON list from the file named first, prints the models as JSON.
// Run by tests/test_i1_tool_review.py.
const fs = require("fs");
const path = require("path");
const L = require(path.join(__dirname, "..", "..", "packages", "desktop", "web", "logic.js"));

const cards = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
process.stdout.write(JSON.stringify(cards.map((c) => L.consentCard(c))));
