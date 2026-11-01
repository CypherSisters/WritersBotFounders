/* The "Web search with your own key" block, next to the GitHub block (github.js): two rows, off unless she links a key.
 * Routes: GET/PUT/DELETE /v1/toolsmith/search-key/<id>. Saving a key raises its own Yes card, naming its one host.
 *
 * THE KEY. As in github.js: read from the field once when she presses Link, sent once, and the field is cleared the
 * moment it is sent. It is never written to localStorage, sessionStorage, a cookie, the address bar or the console,
 * and no Core reply carries it back.
 *
 * The logic part (no DOM) runs under node too (tests/js/searchkeys.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const WHAT_LINE = "Off unless you link your own key. Toolsmith then sends only search words to that one place, while it makes a plan.";
  const KEPT_LINE = "Kept in your vault, never in this browser.";
  const ROWS = [
    { id: "brave", name: "Brave Search", page: "https://api-dashboard.search.brave.com/", get: "Get a key from Brave",
      says: "Brave has no free plan: it charges you for each search, on your own Brave account." },
    { id: "marginalia", name: "Marginalia Search", page: "https://about.marginalia-search.com/article/api/", get: "How to ask Marginalia for a key",
      says: "Marginalia gives its free key by email, so asking for one shares your address with them. That is your choice." },
  ];

  /* The link state from GET /v1/toolsmith/search-key/<id> ({status, data}). */
  function keyView(r) {
    const d = r && r.status === 200 && r.data && typeof r.data === "object" ? r.data : null;
    if (!d) return { state: "error", title: "Could not be read", line: "", form: false, unlink: false };
    const line = String(d.line || "");
    if (d.linked === true) return { state: d.yes ? "linked" : "waiting", title: d.yes ? "Linked" : "Linked, waiting for your Yes", line: line, form: !d.yes, unlink: true };
    if (d.linked === false) return { state: "not_linked", title: "Not linked", line: line, form: true, unlink: false };
    if (d.vault === "not_set_up") return { state: "no_vault", title: "Not linked", line: line, form: false, unlink: false };
    return { state: "cant_tell", title: "Can't tell while your vault is locked", line: line, form: true, unlink: false };
  }

  /* A pasted key, checked the way the Core checks it (web_key_ok). The text itself is never put in the line. */
  const SHAPE = /^[A-Za-z0-9\-._~+/]{6,255}=*$/;
  function keyShape(text, name) {
    const t = String(text || "").trim();
    if (!t) return { ok: false, line: "Paste the key first." };
    if (t.length > 255 || !SHAPE.test(t)) return { ok: false, line: "That does not look like a " + name + " key. Copy the whole key and paste it on one line." };
    return { ok: true, line: "" };
  }

  function drawRow(P, row) {
    const el = P.el, clear = P.clear;
    const box = el("div", { "data-sk": row.id });
    const route = "/v1/toolsmith/search-key/" + row.id;

    async function draw(note) {
      const r = await P.api("GET", route);
      clear(box);
      box.appendChild(el("h3", { text: row.name }));
      if (P.missing(r)) { box.appendChild(P.later("the search key route (GET " + route + ")")); return; }
      const v = keyView(r);
      const said = el("p", { class: "small", "data-sk": "said", text: note || "" });
      const cardBox = el("div");
      box.append(el("p", { class: "now", "data-sk-state": v.state }, [el("b", { text: v.title + ". " }), v.line || (v.state === "error" ? P.said(r) : "")]),
        el("p", { class: "small muted", text: row.says }));
      if (v.unlink) {
        const ask = el("div", { class: "row" });
        const unlink = el("button", { "data-sk": "unlink" }, [P.icon("link_off"), "Remove the key"]);
        unlink.addEventListener("click", () => {
          clear(ask).append(el("span", { class: "small grow", text: "Remove your " + row.name + " key? Toolsmith stops using " + row.name + "." }),
            el("button", { class: "danger", on: { click: async () => {
              const x = await P.api("DELETE", route);
              draw(x.status === 200 ? (x.data && x.data.line) || "Removed." : P.said(x));
            } } }, ["Yes, remove it"]),
            el("button", { class: "link", on: { click: () => draw() } }, ["Keep it"]));
        });
        ask.appendChild(unlink);
        box.appendChild(ask);
      }
      if (v.form) {
        const field = el("input", { type: "password", "aria-label": row.name + " key", placeholder: "Paste the key", autocomplete: "off",
                                    spellcheck: "false", maxlength: "300" });
        const go = el("button", { class: "primary", type: "submit" }, [P.icon("link"), "Link"]);
        const form = el("form", { autocomplete: "off" }, [
          el("div", { class: "row" }, [el("a", { href: row.page, target: "_blank", rel: "noopener noreferrer", text: row.get })]),
          el("div", { class: "row" }, [field, go]),
          el("p", { class: "faint small", text: KEPT_LINE })]);
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const shape = keyShape(field.value, row.name);
          if (!shape.ok) { field.value = ""; said.textContent = shape.line; return; }
          let value = field.value.trim();
          field.value = "";                             // cleared the moment it is sent
          go.disabled = true;
          const x = await P.api("PUT", route, { value: value });
          value = "";
          go.disabled = false;
          if (x.status !== 200 && x.status !== 202) { said.textContent = P.said(x, "That was not saved."); return; }
          if (!x.data || !x.data.consent_id) { draw((x.data && x.data.line) || "Saved."); return; }
          said.textContent = "Saved. Its Yes card is below, and on Your turn.";
          P.consentInline(cardBox, x.data.consent_id, (answer) => {
            draw(answer === "yes" ? "Your Yes is kept. Toolsmith may use " + row.name + " while its search is on." : row.name + " stays off.");
          });
        });
        box.appendChild(form);
      }
      box.append(said, cardBox);
    }
    draw();
    return box;
  }

  /* ---- the block (DOM, with window.Puff) ---- */
  function block() {
    const P = root.Puff, el = P.el;
    const t = P.tile({ id: "search-keys", icon: "travel_explore", title: "Web search with your own key", wide: true });
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }));
    ROWS.forEach((row) => t.body.appendChild(drawRow(P, row)));
    return t.node;
  }

  const api = { ROWS: ROWS, WHAT_LINE: WHAT_LINE, keyView: keyView, keyShape: keyShape, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffSearchKeys = api;
})(typeof window !== "undefined" ? window : this);
