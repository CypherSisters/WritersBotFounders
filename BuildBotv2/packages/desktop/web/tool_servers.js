/* The "Add my own tool" block on Developer > Brain (D137): an outside MCP tool server by its web address.
 * Routes: GET/POST /v1/tools/servers, DELETE /v1/tools/servers/<id>, POST /v1/tools/servers/<id>/refresh.
 *
 * A password is read from its box once, sent in the POST body only, and the box is cleared after the send. It is never
 * kept here, never put in an address, and the Core never sends it back (only "has_password").
 *
 * NOTHING HERE GRANTS. A server off this computer waits for the Bridge's address card: the Core's reply carries it and
 * P.consentInline shows it, the same card as on Your turn. After her Yes, Refresh connects.
 *
 * Every word from the Core, and a server's name above all, goes on the page as text. This file never writes HTML.
 *
 * The logic part (no DOM) runs under node too (tests/js/tool_servers.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const TITLE = "Add my own tool";
  const WHAT_LINE = "Add an MCP tool server by its web address. BuildBot connects to it first and keeps it only if it answers. Its tools then show up for the brain. Any password goes into your vault.";
  const ADDRESS_HELP = "Its web address, like http://127.0.0.1:8000/mcp. One on your home network or the internet also needs your Yes on the Bridge page. A command that starts a server is not taken here yet.";
  const NONE_LINE = "No tool servers added yet.";
  const ADD = "Add this tool server";

  /* What she typed, checked before it is sent. The Core checks it all again, and only the Core decides. */
  function formProblem(f) {
    if (!String(f.name || "").trim()) return "Give it a short name.";
    const address = String(f.address || "").trim();
    if (!address) return "Type its web address.";
    if (!/^https?:\/\//i.test(address)) return "Give a web address that starts with http:// or https://.";
    return "";
  }

  /* The body for POST: the name, the address, and the password only when one was typed. */
  function formBody(f) {
    const body = { name: String(f.name || "").trim(), address: String(f.address || "").trim() };
    const password = String(f.password || "").trim();
    if (password) body.password = password;
    return body;
  }

  /* One row from GET /v1/tools/servers, in her words. The fixed words here never come from a reply. */
  function rowView(r) {
    const ready = r.state === "ready";
    const n = Number.isFinite(r.tool_count) ? r.tool_count : (Array.isArray(r.tools) ? r.tools.length : 0);
    return {
      id: String(r.id || ""),
      name: String(r.name || r.id || ""),
      address: String(r.address || ""),
      url: String(r.url || ""),
      where: String(r.where_said || ""),
      tools: n,
      toolsWord: n + (n === 1 ? " tool" : " tools"),
      password: r.has_password === true,
      state: ready ? "ready" : "waiting",
      stateWord: ready ? "Ready" : "Waiting",
      line: String(r.line || ""),
    };
  }

  /* What a POST answered: the card to show, or only a line. */
  function replyView(reply) {
    const d = reply && reply.data && typeof reply.data === "object" ? reply.data : {};
    const ok = !!reply && reply.status >= 200 && reply.status < 300;
    return { ok: ok, consentId: ok && typeof d.consent_id === "string" ? d.consent_id : "", line: String(d.line || d.error || (ok ? "" : "That did not work.")) };
  }

  function drawRow(P, row, redraw) {
    const v = rowView(row);
    const el = P.el;
    const said = el("p", { class: "small", "data-server": "said" });
    const cardBox = el("div", { "data-server": "card" });
    const remove = el("button", { class: "link", "data-server": "remove", on: { click: async () => {
      remove.disabled = true;
      const x = await P.api("DELETE", "/v1/tools/servers/" + encodeURIComponent(v.id));
      remove.disabled = false;
      if (x.status !== 200) { said.textContent = P.said(x, "That was not removed."); return; }
      redraw(x.data && x.data.line ? String(x.data.line) : "Removed.");
    } } }, ["Remove"]);
    const refresh = el("button", { class: "link", "data-server": "refresh", on: { click: async () => {
      refresh.disabled = true;
      const x = await P.api("POST", "/v1/tools/servers/" + encodeURIComponent(v.id) + "/refresh");
      refresh.disabled = false;
      const r = replyView(x);
      if (x.status === 200) { redraw(r.line); return; }
      said.textContent = r.line;
      if (r.consentId) { P.clear(cardBox); P.consentInline(cardBox, r.consentId, () => redraw()); }
    } } }, ["Refresh"]);
    return el("div", { "data-server-row": v.id, "data-server-state": v.state }, [
      el("h3", { text: v.name }),
      el("p", { class: "small" }, [el("b", { text: v.address }), ", ", el("span", { "data-server": "where", text: v.where }), ", ",
        el("span", { "data-server": "tools", text: v.toolsWord }), v.password ? ", its password is in your vault" : ""]),
      el("p", { class: "now" }, [el("b", { "data-server": "state", text: v.stateWord + ". " }), v.line]),
      el("div", { class: "row" }, [refresh, remove]), said, cardBox]);
  }

  /* The form: a name, a web address, a password if it needs one. A server off this computer shows its Yes card here. */
  function form(P, done) {
    const el = P.el;
    const name = el("input", { type: "text", "aria-label": "Name", placeholder: "my_tools", autocomplete: "off", spellcheck: "false", maxlength: "60" });
    const address = el("input", { class: "grow", type: "text", "aria-label": "Web address", placeholder: "http://127.0.0.1:8000/mcp", autocomplete: "off", spellcheck: "false", maxlength: "300" });
    const password = el("input", { type: "password", "aria-label": "Password, if it needs one", placeholder: "Password, if it needs one", autocomplete: "off", spellcheck: "false", maxlength: "4096" });
    const said = el("p", { class: "small", "data-server": "form-said" });
    const cardBox = el("div", { "data-server": "card" });
    const go = el("button", { class: "primary", type: "submit" }, [P.icon("add"), ADD]);
    const f = el("form", { autocomplete: "off", "data-server": "form" }, [el("p", { class: "small muted", text: ADDRESS_HELP }),
      el("div", { class: "row" }, [name, address]), el("div", { class: "row" }, [password]), el("div", { class: "row" }, [go]), said, cardBox]);
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const given = { name: name.value, address: address.value, password: password.value };
      password.value = "";                           // read once: the box is empty again before anything is sent
      const problem = formProblem(given);
      said.textContent = problem;
      if (problem) { given.password = ""; return; }
      P.clear(cardBox);
      go.disabled = true;
      const body = formBody(given);
      given.password = "";
      const r = await P.api("POST", "/v1/tools/servers", body);
      delete body.password;
      go.disabled = false;
      const v = replyView(r);
      said.textContent = v.line;
      if (!v.ok) return;
      name.value = "";
      address.value = "";
      done();
      if (v.consentId) P.consentInline(cardBox, v.consentId, () => done());
    });
    return f;
  }

  /* ---- the block (DOM, with window.Puff) ---- */
  function block(given) {
    const P = given || root.Puff, el = P.el;
    const t = P.tile({ id: "tool-servers", icon: "extension", title: TITLE, wide: true });
    const list = el("div", { "data-server": "list" });
    const note = el("p", { class: "small", "data-server": "note" });
    const formBox = el("div");
    let drawn = false;
    async function draw(line) {
      const r = await P.api("GET", "/v1/tools/servers");
      P.clear(list);
      note.textContent = line || "";
      if (P.missing(r)) { list.appendChild(P.later("the tool servers route (GET /v1/tools/servers)")); return; }
      if (r.status !== 200) { note.textContent = P.said(r); return; }
      const d = r.data && typeof r.data === "object" ? r.data : {};
      const rows = Array.isArray(d.servers) ? d.servers : [];
      rows.forEach((row) => list.appendChild(drawRow(P, row, draw)));
      if (!rows.length) list.appendChild(el("p", { class: "small muted", text: NONE_LINE }));
      if (!drawn) {                                  // once: a redraw never takes away the card or what she is typing
        drawn = true;
        formBox.appendChild(form(P, () => draw()));
      }
    }
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }), list, note, formBox);
    draw();
    return t.node;
  }

  const api = { TITLE: TITLE, WHAT_LINE: WHAT_LINE, ADD: ADD, formProblem: formProblem, formBody: formBody, rowView: rowView, replyView: replyView, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffToolServers = api;
})(typeof window !== "undefined" ? window : this);
