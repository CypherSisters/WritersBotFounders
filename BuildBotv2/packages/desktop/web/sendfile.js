/* Hand a file to an app that takes files (D127; 02 `accepts`): GET /v1/packages/sendable, POST /v1/packages/<id>/send,
 * and the bot's own Open dialog for documents and folders (POST /v1/knowledge/pick). Shown on Apps and on Knowledge. Where that
 * dialog can't show (another device, or a bot that has none), the browser's own file input and an upload (D134). */
(function () {
  "use strict";
  const P = window.Puff, D = window.PuffDev, L = P.L, el = P.el, clear = P.clear;

  /* D140: a fix the bot shows in chat, in the window and the dashboard: the passage highlighted where it is, and the
   * suggested wording. She makes the change; nothing in the file changed. */
  P.passageCard = function (p) {
    return el("div", { class: "passage-card", "data-passage": "" }, [
      el("div", { class: "small faint", text: p.where }),
      el("p", { class: "reading" }, [p.before ? (p.before.length >= 240 ? "..." : "") + p.before : "", el("mark", { text: p.passage }), p.after ? p.after + (p.after.length >= 240 ? "..." : "") : ""]),
      p.suggestion ? el("p", {}, [el("b", { text: "Suggested: " }), p.suggestion]) : null,
      p.why ? el("p", { class: "small", text: p.why }) : null,
      el("p", { class: "small faint", text: "Nothing in your file changed. The change is yours to make." })]);
  };

  /* D140: the one setting, in the window's Settings and the dashboard's. Off until she turns it on. */
  P.editsSetting = function () {
    const box = el("input", { type: "checkbox", "data-edits": "", "aria-label": "Let your bot change your documents" });
    const said = el("div", { class: "small faint", "data-edits-said": "" });
    const words = (on) => { said.textContent = on
      ? "On: your bot can change Word, Excel and PowerPoint files, and the real text it has goes in the file. A file it did not make asks your Yes first, one file at a time, and a linked file keeps a copy of the original."
      : "Off: your bot shows you a fix and highlights the passage it means, and you make the change. It can still make a new file."; };
    words(false);
    P.api("GET", "/v1/settings/edits").then((r) => { box.checked = r.status === 200 && r.data.on === true; words(box.checked); });
    box.addEventListener("change", async () => {
      const r = await P.api("PUT", "/v1/settings/edits", { on: box.checked });
      if (r.status !== 200) { box.checked = !box.checked; said.textContent = P.said(r); return; }
      box.checked = r.data.on === true;
      words(box.checked);
    });
    return el("div", {}, [el("label", { class: "row" }, [box, el("b", { text: "Let your bot change your documents" })]), said]);
  };

  P.sendTargets = async function () {
    const r = await P.api("GET", "/v1/packages/sendable");
    return r.status === 200 && Array.isArray(r.data) ? r.data : [];
  };

  async function send(target, body, said) {
    clear(said).appendChild(el("p", { class: "small", text: "Sending to " + target.name + "..." }));
    const x = await P.api("POST", "/v1/packages/" + encodeURIComponent(target.id) + "/send", body);
    clear(said).appendChild(el("p", { class: x.status === 200 ? "small" : "fail", "data-sent": target.id, text: x.status === 200 ? String((x.data || {}).line || "Sent.") : P.said(x) }));
  }

  /* On an app's own row: pick any file on this device. */
  P.sendFileButton = function (target, said) {
    const input = el("input", { type: "file", hidden: "hidden", accept: (target.types || []).join(","), "aria-label": "A file to send to " + target.name });
    input.addEventListener("change", () => {
      const file = input.files && input.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        input.value = "";
        send(target, { file_name: file.name, content_b64: D.dataUrlBase64(reader.result) }, said);
      };
      reader.onerror = () => clear(said).appendChild(el("p", { class: "fail", text: "That file could not be read." }));
      reader.readAsDataURL(file);
    });
    return el("span", {}, [input, el("button", { "data-act": "send", on: { click: () => input.click() } }, [P.icon("upload_file"), "Send a file to it"])]);
  };

  /* On a document's row: "Send to..." opens the apps that take this kind of file. */
  P.sendPathButton = function (path, name, targets, said) {
    const fit = D.sendFits(targets, name);
    if (!fit.length) return null;
    const list = el("div", { class: "row", hidden: "hidden", "data-send-to": name }, fit.map((t) => el("button", { on: { click: () => send(t, { path: path }, said) } }, [t.name])));
    const open = el("button", { class: "link", text: "Send to...", on: { click: () => { list.hidden = !list.hidden; } } });
    return el("div", {}, [open, list]);
  };

  /* Windows's own Open dialog: the path (with kind "files", a list of paths), or null if she closed it, or { upload: true }
   * when there is no such dialog here, or for "files" when it can't open, and the file has to come from this browser's
   * own file input (D134). */
  P.pickPath = async function (kind, said) {
    said.textContent = "Look for the window that just opened on this computer.";
    const x = await P.api("POST", "/v1/knowledge/pick", { kind: kind });
    said.textContent = "";
    if (x.status !== 200) { said.textContent = P.said(x); return kind === "files" && x.status === 409 ? { upload: true } : null; }
    const d = x.data || {};
    if (d.typed) { said.textContent = String(d.line || ""); return d.upload ? { upload: true } : null; }
    return (d.paths && d.paths.length ? d.paths : d.path) || null;
  };

  /* D134: one file from this device goes to the bot, read as it goes. It answers { said, ok } or { error }. */
  async function upload(file) {
    const problem = L.uploadProblem(file.name, file.size);
    if (problem) return { error: problem };
    const x = await P.api("POST", "/v1/knowledge/upload?name=" + encodeURIComponent(file.name), file);
    if (x.status !== 200) return { error: x.status === 413 ? L.UPLOAD_TOO_BIG : P.said(x, "That file was not saved.") };
    return { said: (x.data || {}).said, ok: (x.data || {}).ok };
  }

  /* D134: the browser's own file input, for one file or many. *chosen* gets the files and the way to send each. */
  P.uploadInput = function (chosen) {
    const input = el("input", { type: "file", hidden: "hidden", multiple: "multiple", accept: L.UPLOAD_TYPES, "data-upload": "", "aria-label": "Files from this device" });
    input.addEventListener("change", () => {
      const files = Array.from(input.files || []);   // first: clearing the input empties its list
      input.value = "";              // so the same file, changed, can be picked again
      if (files.length) chosen(files, upload);
    });
    return input;
  };
})();
