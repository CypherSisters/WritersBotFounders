// D134: a file from the device she is at. Run by tests/test_d134_upload.py.
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const web = path.join(__dirname, "..", "..", "packages", "desktop", "web");
const L = require(path.join(web, "logic.js"));
const read = (name) => fs.readFileSync(path.join(web, name), "utf8");

// Said before anything is sent. The Core says the same again.
assert.strictEqual(L.uploadProblem("a.txt", 5), "");
assert.strictEqual(L.uploadProblem("A Novel.DOCX", 5), "");
assert.strictEqual(L.uploadProblem("a.pdf", L.UPLOAD_MAX), "");
assert.strictEqual(L.uploadProblem("a.pdf", L.UPLOAD_MAX + 1), L.UPLOAD_TOO_BIG);
assert.ok(/50 MB/.test(L.UPLOAD_TOO_BIG));
assert.ok(/empty/.test(L.uploadProblem("a.md", 0)));
for (const n of ["keys.pem", "novel", "a.txt.exe", "", null]) assert.ok(/\.txt, \.md/.test(L.uploadProblem(n, 5)), String(n));
assert.strictEqual(L.UPLOAD_TYPES, ".txt,.md,.docx,.pdf");

assert.strictEqual(L.onNetworkHost("192.168.1.5"), true);
assert.strictEqual(L.onNetworkHost("puffpai.localhost"), false);

// The file itself goes as the body, in the dashboard and in the window: not base64, not JSON.
for (const name of ["desktop.js", "window.js"]) {
  const js = read(name);
  assert.ok(js.includes('body instanceof Blob'), name);
  assert.ok(js.includes('"application/octet-stream"'), name);
}

// One button: the browser's own picker opens at once on another device, the Windows dialog on the bot's own computer.
// The clicks themselves run in tests/js/v3_add_documents.test.js.
const k = read("pages/knowledge.js");
assert.ok(k.includes("L.onNetworkHost(location.hostname)"));
assert.ok(k.includes("fromDevice.click()"));
assert.ok(k.includes('dialog("files")'));                       // and only there
assert.ok(k.indexOf("fromDevice.click(); return; }    // at once") < k.indexOf('dialog("files")'));
assert.ok(k.includes("got && got.upload"));                     // a bot with no dialog falls back the same way
assert.ok(!k.includes('"/v1/knowledge/pinned", {'));            // what is sent is on the list by itself: the page never pins it

const s = read("sendfile.js");
assert.ok(s.includes('type: "file"') && s.includes("L.UPLOAD_TYPES"));
assert.ok(s.includes('multiple: "multiple"'));
assert.ok(s.includes('"/v1/knowledge/upload?name=" + encodeURIComponent(file.name)'));
assert.ok(s.includes("L.uploadProblem(file.name, file.size)"));
assert.ok(s.includes('input.value = ""'));                      // the same file, changed, can be picked again
assert.ok(s.indexOf("Array.from(input.files") < s.lastIndexOf('input.value = ""'));   // clearing the input empties its list

// Nothing on another device says it opens on the bot's computer for this any more.
for (const line of k.split("\n").concat(s.split("\n"))) {
  if (/upload|fromDevice|FOLDER_FROM_DEVICE/.test(line)) assert.ok(!/bot's computer|bot's own computer/.test(line), line);
}

// The device list is built: no "not built" line for it.
assert.strictEqual(L.NOT_BUILT.devices, undefined);
const st = read("pages/settings.js");
assert.ok(st.includes('"/v1/devices"') && st.includes('"DELETE"') && st.includes('text: "Remove"'));

// Both pages load what this needs.
assert.ok(read("index.html").includes("sendfile.js") && read("window.html").includes("sendfile.js"));
console.log("ok");
