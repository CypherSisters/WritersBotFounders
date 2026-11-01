/* The "Add an API" block on Developer: any search API the owner adds becomes a place Toolsmith searches, with no code.
 * Routes: GET/POST /v1/toolsmith/apis, PUT/DELETE /v1/toolsmith/apis/<id>, PUT/DELETE .../key, POST .../test.
 * The window's Settings uses these same routes.
 *
 * THE KEY. As in searchkeys.js: read from the field once when she presses Save, sent once, and the field is cleared the
 * moment it is sent. It is never written to localStorage, sessionStorage, a cookie, the address bar or the console, and
 * no Core reply carries it back.
 *
 * The logic part (no DOM) runs under node too (tests/js/addapi.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const WHAT_LINE = "Add any search API you use. After you test it and say Yes, Toolsmith searches it on every plan, sending only search words to that one place. What it finds shows as links to look at.";
  const ADDRESS_HELP = "Its https search address, with {q} where the search words go. For example: https://api.example.com/search?query={q}";
  const KEPT_LINE = "Kept in your vault, never in this browser.";
  const FIELDS_LINE = "Toolsmith could not read its answers by itself. Name the fields, using dots for nested ones, like web.results, and test again.";
  const WHERE = [
    { value: "header", label: "In a header (most APIs)" },
    { value: "bearer", label: "As a Bearer token (Authorization header)" },
    { value: "query", label: "In the address (only if that is all the API takes)" },
    { value: "none", label: "It needs no key" },
  ];
  const FIELD_NAMES = [
    { id: "list", label: "The list of results" }, { id: "title", label: "Each result's title" },
    { id: "link", label: "Each result's link" }, { id: "snippet", label: "Each result's text" },
  ];

  /* What she typed, checked before it is sent; the Core checks it all again. The key's text is never in a line. */
  function formProblem(f) {
    const name = String(f.name || "").trim(), search = String(f.search || "").trim();
    if (!name) return "Give it a name.";
    if (!search) return "Paste its search address.";
    if (!/^https:\/\//i.test(search)) return "The address has to start with https://.";
    if ((search.match(/\{q\}/g) || []).length !== 1) return "Put {q} once in the address, where the search words go.";
    if (f.key_in !== "none" && f.key_in !== "bearer" && !String(f.key_name || "").trim()) return "Say the name of the header or address field the key goes in.";
    return "";
  }

  /* The body for POST or PUT: never the key. */
  function formBody(f) {
    const fields = {};
    FIELD_NAMES.forEach((n) => { const v = String((f.fields || {})[n.id] || "").trim(); if (v) fields[n.id] = v; });
    const out = { name: String(f.name || "").trim(), search: String(f.search || "").trim(), key_in: f.key_in || "header" };
    if (out.key_in === "bearer") { out.key_in = "header"; out.key_name = "Authorization"; out.key_prefix = "Bearer"; }
    else if (out.key_in !== "none") { out.key_name = String(f.key_name || "").trim(); out.key_prefix = ""; }
    if (Object.keys(fields).length) out.fields = fields;
    return out;
  }

  /* One row's state, in her words, from GET /v1/toolsmith/apis. */
  function rowView(r) {
    const needsKey = r.key_in !== "none";
    if (r.on === true) return { state: "on", title: "On", line: r.line || "Toolsmith searches it on every plan." };
    if (needsKey && r.linked === false) return { state: "needs_key", title: "Off", line: r.line || "It needs its key." };
    if (r.tested !== true && r.yes === true) return { state: "needs_test", title: "Off", line: r.line || "It stays off until a test reads results." };
    if (r.yes !== true) return { state: r.tested === true ? "needs_yes" : "needs_test", title: "Off", line: r.line || "It waits for your Yes and a test." };
    return { state: "off", title: "Off", line: r.line || "" };
  }

  /* A Test reply, as lines and links. A link is shown only as a plain https address, to open in a new tab. */
  function testView(reply) {
    const d = reply && reply.data && typeof reply.data === "object" ? reply.data : {};
    const rows = (Array.isArray(d.rows) ? d.rows : []).filter((x) => x && /^https:\/\//.test(String(x.url || "")) && !/[\s@]/.test(String(x.url)));
    const ok = reply && reply.status === 200 && d.ok === true && rows.length > 0;
    return { ok: ok, line: String(d.line || d.error || (ok ? "" : "That did not work.")), rows: ok ? rows.slice(0, 5) : [], askFields: !ok && d.answered === true };
  }

  function select(P, value, label) {
    const box = P.el("select", { "aria-label": label });
    WHERE.forEach((w) => {
      const o = P.el("option", { value: w.value, text: w.label });
      if (w.value === value) o.selected = true;
      box.appendChild(o);
    });
    return box;
  }

  function fieldInputs(P, have) {
    const inputs = {};
    const lines = FIELD_NAMES.map((n) => {
      inputs[n.id] = P.el("input", { type: "text", "aria-label": n.label, placeholder: n.label, spellcheck: "false", maxlength: "100", value: String((have || {})[n.id] || "") });
      return P.el("div", { class: "row" }, [inputs[n.id]]);
    });
    return { inputs, lines };
  }

  function card(P, into, consent, after) {
    if (consent) P.consentInline(into, consent, after);
  }

  /* The add or edit form. *row*: the row to edit, or null to add. */
  function form(P, row, done) {
    const el = P.el;
    const name = el("input", { class: "grow", type: "text", "aria-label": "Name", placeholder: "Name", maxlength: "60", value: row ? row.name : "" });
    const search = el("input", { class: "grow", type: "text", "aria-label": "Search address", placeholder: "https://api.example.com/search?query={q}", spellcheck: "false", maxlength: "500", value: row ? row.search : "" });
    const where = select(P, row ? (row.key_prefix === "Bearer" ? "bearer" : row.key_in) : "header", "Where the key goes");
    const keyName = el("input", { type: "text", "aria-label": "Name of the header or address field", placeholder: "Its name, like X-Api-Key", spellcheck: "false", maxlength: "40", value: row && row.key_name ? row.key_name : "" });
    const fields = fieldInputs(P, row && row.fields);
    const said = el("p", { class: "small" });
    const go = el("button", { class: "primary", type: "submit" }, [P.icon("add"), row ? "Save" : "Add"]);
    const noName = () => where.value === "none" || where.value === "bearer";
    where.addEventListener("change", () => { keyName.hidden = noName(); });
    keyName.hidden = noName();
    const f = el("form", { autocomplete: "off" }, [el("p", { class: "small muted", text: ADDRESS_HELP }), el("div", { class: "row" }, [name]), el("div", { class: "row" }, [search]),
      el("div", { class: "row" }, [where, keyName]), el("details", {}, [el("summary", { text: "Name the fields (only if a test asks)" })].concat(fields.lines)), el("div", { class: "row" }, [go]), said]);
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const given = { name: name.value, search: search.value, key_in: where.value, key_name: keyName.value, fields: {} };
      FIELD_NAMES.forEach((n) => { given.fields[n.id] = fields.inputs[n.id].value; });
      const problem = formProblem(given);
      if (problem) { said.textContent = problem; return; }
      go.disabled = true;
      const r = row ? await P.api("PUT", "/v1/toolsmith/apis/" + encodeURIComponent(row.id), formBody(given)) : await P.api("POST", "/v1/toolsmith/apis", formBody(given));
      go.disabled = false;
      if (r.status >= 400) { said.textContent = P.said(r, "That was not saved."); return; }
      done(r.data || {});
    });
    return f;
  }

  function drawRow(P, row, redraw) {
    const el = P.el;
    const route = "/v1/toolsmith/apis/" + encodeURIComponent(row.id);
    const v = rowView(row);
    const box = el("div", { "data-api": row.id });
    const said = el("p", { class: "small", "data-api": "said" });
    const cardBox = el("div");
    const found = el("div");
    const now = el("p", { class: "now", "data-api-state": v.state }, [el("b", { text: v.title + ". " }), v.line]);
    box.append(el("h3", { text: row.name }), now, el("p", { class: "small muted", text: "Reaches " + row.host + "." }));

    if (row.key_in !== "none") {
      const field = el("input", { type: "password", "aria-label": row.name + " key", placeholder: row.linked ? "Paste a new key" : "Paste the key", autocomplete: "off", spellcheck: "false", maxlength: "300" });
      const save = el("button", { type: "submit" }, [P.icon("key"), "Save the key"]);
      const keyForm = el("form", { autocomplete: "off" }, [el("div", { class: "row" }, [field, save]), el("p", { class: "faint small", text: KEPT_LINE })]);
      keyForm.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        let value = field.value.trim();
        field.value = "";                                // cleared the moment it is sent
        if (!value) { said.textContent = "Paste the key first."; return; }
        save.disabled = true;
        const x = await P.api("PUT", route + "/key", { value: value });
        value = "";
        save.disabled = false;
        if (x.status !== 200 && x.status !== 202) { said.textContent = P.said(x, "That was not saved."); return; }
        if (!x.data || !x.data.consent_id) { redraw((x.data && x.data.line) || "Saved."); return; }
        said.textContent = "Saved. Its Yes card is below, and on Your turn.";
        card(P, cardBox, x.data.consent_id, (answer) => redraw(answer === "yes" ? "Your Yes is kept." : row.name + " stays off."));
      });
      box.appendChild(keyForm);
    }

    const word = el("input", { type: "text", "aria-label": "A word to test with", placeholder: "A word to test with", maxlength: "60", value: "weather" });
    const test = el("button", { type: "submit" }, [P.icon("play_arrow"), "Test"]);
    const testForm = el("form", { autocomplete: "off" }, [el("div", { class: "row" }, [word, test])]);
    testForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      test.disabled = true;
      const x = await P.api("POST", route + "/test", { word: word.value });
      test.disabled = false;
      const t = testView(x);
      P.clear(found);
      found.appendChild(el("p", { class: "small", text: t.line }));
      t.rows.forEach((r) => found.appendChild(el("p", { class: "small" }, [el("a", { href: r.url, target: "_blank", rel: "noopener noreferrer", text: String(r.name || r.url) })])));
      if (t.askFields) found.appendChild(el("p", { class: "small muted", text: FIELDS_LINE }));
      if (!t.ok) return;
      // Its state again, in place, so the results she just got stay on the page.
      const fresh = await P.api("GET", "/v1/toolsmith/apis");
      const mine = fresh.status === 200 && Array.isArray(fresh.data && fresh.data.apis) ? fresh.data.apis.find((a) => a.id === row.id) : null;
      if (!mine) return;
      const nowV = rowView(mine);
      P.clear(now).append(el("b", { text: nowV.title + ". " }), nowV.line);
      now.setAttribute("data-api-state", nowV.state);
    });
    box.append(testForm, found);

    const actions = el("div", { class: "row" });
    const ask = el("div", { class: "row" });
    actions.append(el("button", { class: "link", on: { click: () => {
      P.clear(ask).appendChild(form(P, row, (d) => {
        redraw(d.line || "Saved.");
        if (d.consent_id) card(P, cardBox, d.consent_id, () => redraw());
      }));
    } } }, ["Change it"]), el("button", { class: "link", "data-api": "remove", on: { click: () => {
      const dropKey = el("input", { type: "checkbox", checked: true, "aria-label": "Also take its key out of your vault" });
      P.clear(ask).append(el("span", { class: "small grow", text: "Remove " + row.name + "? Toolsmith stops searching it." }),
        row.key_in !== "none" ? el("label", { class: "opt" }, [dropKey, el("span", { text: "Also take its key out of your vault" })]) : null,
        el("button", { class: "danger", on: { click: async () => {
          const x = await P.api("DELETE", route, { remove_key: row.key_in !== "none" && dropKey.checked });
          redraw(x.status === 200 ? (x.data && x.data.line) || "Removed." : P.said(x));
        } } }, ["Yes, remove it"]), el("button", { class: "link", on: { click: () => redraw() } }, ["Keep it"]));
    } } }, ["Remove"]));
    box.append(actions, ask, said, cardBox);
    return box;
  }

  /* ---- the block (DOM, with window.Puff) ---- */
  function block() {
    const P = root.Puff, el = P.el;
    const t = P.tile({ id: "add-api", icon: "api", title: "Add an API", wide: true });
    const list = el("div");
    const note = el("p", { class: "small", "data-api": "note" });
    const cardBox = el("div");
    async function draw(line) {
      const r = await P.api("GET", "/v1/toolsmith/apis");
      P.clear(list);
      note.textContent = line || "";
      if (P.missing(r)) { list.appendChild(P.later("the API route (GET /v1/toolsmith/apis)")); return; }
      if (r.status !== 200) { note.textContent = P.said(r); return; }
      const rows = Array.isArray(r.data && r.data.apis) ? r.data.apis : [];
      rows.forEach((row) => list.appendChild(drawRow(P, row, draw)));
      if (!rows.length) list.appendChild(el("p", { class: "small muted", text: "None added yet." }));
    }
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }), list, el("h3", { text: "Add one" }), form(P, null, (d) => {
      draw(d.line || "Added. Save its key if it has one, then test it.");
      if (d.consent_id) P.consentInline(cardBox, d.consent_id, () => draw());
    }), note, cardBox);
    draw();
    return t.node;
  }

  const api = { WHAT_LINE: WHAT_LINE, WHERE: WHERE, FIELD_NAMES: FIELD_NAMES, formProblem: formProblem, formBody: formBody, rowView: rowView, testView: testView, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffAddApi = api;
})(typeof window !== "undefined" ? window : this);
