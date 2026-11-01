/* The "Addresses you allowed for packages" block on the Bridge page (D110): one address and port for one package.
 * Routes: GET/POST /v1/bridge/addresses, DELETE /v1/bridge/addresses/<consent_id>.
 *
 * NOTHING HERE GRANTS. The form asks the Core for the Yes card and shows it with P.consentInline, the same card as on Your
 * turn. Only her answer on that card makes a grant, and that answer is the Desktop's own (desktop.js), never this file's.
 *
 * Every word from the Core, and a package's name above all, goes on the page as text (P.el's text, or a text node).
 * This file never writes HTML.
 *
 * The logic part (no DOM) runs under node too (tests/js/addresses.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const TITLE = "Addresses you allowed for packages";
  const WHAT_LINE = "Allow one address and port for one package, on your home network or on the internet. Each one needs its own Yes, and you can remove it any time. Only that package, only that address and that port.";
  const ADDRESS_HELP = "Type the address and port, like 192.168.1.20:8123 or printer.local:8123, or a site's name and port, like news.example.com:443. Starting with https:// means port 443, and http:// means port 80. No path, no user name, no wildcard.";
  const NONE_LINE = "None allowed yet.";
  const ALLOW = "Allow this address and port";

  /* What she typed, checked before it is sent. The Core checks it all again, and only the Core decides. */
  function formProblem(f) {
    if (!String(f.package || "").trim()) return "Pick a package first.";
    if (!String(f.address || "").trim()) return "Type an address and port.";
    return "";
  }

  /* The body for POST: the package and what she typed, nothing else. */
  function formBody(f) {
    return { package: String(f.package || "").trim(), address: String(f.address || "").trim() };
  }

  /* One row from GET /v1/bridge/addresses, in her words. The fixed words here never come from a reply. */
  function rowView(r) {
    const on = r.state === "on";
    return {
      id: String(r.consent_id || ""),
      name: String(r.name || r.package || ""),
      package: String(r.package || ""),
      address: String(r.address || ""),
      port: String(r.port === undefined || r.port === null ? "" : r.port),
      where: r.where === "internet" ? "internet" : "home network",
      state: on ? "on" : "waiting",
      stateWord: on ? "On" : "Waiting",
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
    const said = el("p", { class: "small", "data-address": "said" });
    const remove = el("button", { class: "link", "data-address": "remove", on: { click: async () => {
      remove.disabled = true;
      const x = await P.api("DELETE", "/v1/bridge/addresses/" + encodeURIComponent(v.id));
      remove.disabled = false;
      if (x.status !== 200) { said.textContent = P.said(x, "That was not removed."); return; }
      redraw(x.data && x.data.line ? String(x.data.line) : "Removed.");
    } } }, ["Remove"]);
    return el("div", { "data-address-row": v.id, "data-address-state": v.state }, [
      el("h3", { text: v.name }),
      el("p", { class: "small" }, [el("b", { text: v.address }), " port ", el("b", { text: v.port }), ", ", el("span", { "data-address": "where", text: v.where })]),
      el("p", { class: "now" }, [el("b", { "data-address": "state", text: v.stateWord + ". " }), v.line]),
      el("div", { class: "row" }, [remove]), said]);
  }

  /* The form: pick an installed package, type an address, press the button. That raises the Yes card, shown right here. */
  function form(P, packages, done) {
    const el = P.el;
    const pick = el("select", { "aria-label": "Package" });
    packages.forEach((p) => {
      pick.appendChild(el("option", { value: String(p.id), text: String(p.name || p.id) + " (" + String(p.id) + ")" }));
    });
    const address = el("input", { class: "grow", type: "text", "aria-label": "Address and port", placeholder: "192.168.1.20:8123", autocomplete: "off", spellcheck: "false", maxlength: "300" });
    const said = el("p", { class: "small", "data-address": "form-said" });
    const cardBox = el("div", { "data-address": "card" });
    const go = el("button", { class: "primary", type: "submit" }, [P.icon("add"), ALLOW]);
    const f = el("form", { autocomplete: "off", "data-address": "form" }, [el("p", { class: "small muted", text: ADDRESS_HELP }),
      el("div", { class: "row" }, [pick, address]), el("div", { class: "row" }, [go]), said, cardBox]);
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const given = { package: pick.value, address: address.value };
      const problem = formProblem(given);
      said.textContent = problem;
      if (problem) return;
      P.clear(cardBox);
      go.disabled = true;
      const r = await P.api("POST", "/v1/bridge/addresses", formBody(given));
      go.disabled = false;
      const v = replyView(r);
      said.textContent = v.line;
      if (!v.ok) return;
      address.value = "";
      done();
      if (v.consentId) P.consentInline(cardBox, v.consentId, () => done());
    });
    return f;
  }

  /* ---- the block (DOM, with window.Puff) ---- */
  function block(given) {
    const P = given || root.Puff, el = P.el;
    const t = P.tile({ id: "addresses", icon: "router", title: TITLE, wide: true });
    const list = el("div", { "data-address": "list" });
    const note = el("p", { class: "small", "data-address": "note" });
    const formBox = el("div");
    let drawn = false;
    async function draw(line) {
      const r = await P.api("GET", "/v1/bridge/addresses");
      P.clear(list);
      note.textContent = line || "";
      if (P.missing(r)) { list.appendChild(P.later("the addresses route (GET /v1/bridge/addresses)")); return; }
      if (r.status !== 200) { note.textContent = P.said(r); return; }
      const d = r.data && typeof r.data === "object" ? r.data : {};
      if (!line && d.note) note.textContent = String(d.note);
      const rows = Array.isArray(d.addresses) ? d.addresses : [];
      rows.forEach((row) => list.appendChild(drawRow(P, row, draw)));
      if (!rows.length) list.appendChild(el("p", { class: "small muted", text: NONE_LINE }));
      if (!drawn) {                                  // once: a redraw never takes away the card or what she is typing
        drawn = true;
        const packages = Array.isArray(d.packages) ? d.packages : [];
        formBox.appendChild(form(P, packages, () => draw()));
      }
    }
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }), list, note, formBox);
    draw();
    return t.node;
  }

  const api = { TITLE: TITLE, WHAT_LINE: WHAT_LINE, ALLOW: ALLOW, formProblem: formProblem, formBody: formBody, rowView: rowView, replyView: replyView, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffAddresses = api;
})(typeof window !== "undefined" ? window : this);
