/* A small app made by Toolsmith (D155). It draws what spec.json says, and sets only text: what is kept is never markup. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let spec = null;
  let editing = "";

  function el(tag, props, kids) {
    const n = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
      if (k === "text") n.textContent = v;
      else if (k === "on") Object.entries(v).forEach(([e, f]) => n.addEventListener(e, f));
      else if (k === "checked" || k === "required") n[k] = !!v;
      else n.setAttribute(k, v);
    });
    (kids || []).forEach((c) => n.appendChild(c));
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
  function say(text, bad) { const s = $("said"); s.textContent = text || ""; s.className = bad ? "bad" : ""; }

  async function call(method, path, body) {
    const opts = { method: method, headers: { "X-PuffPAI-App": "1" } };
    if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
    const r = await fetch(path, opts);
    let data = null;
    try { data = await r.json(); } catch (e) { /* no words came back */ }
    if (!r.ok) throw new Error((data && data.error) || "That did not work.");
    return data;
  }

  const INPUT = { text: "text", number: "number", date: "date", link: "url" };
  function fieldInput(f, value) {
    const id = "f-" + f.key;
    if (f.kind === "long") { const t = el("textarea", { id: id, rows: "3" }); t.value = value || ""; return t; }
    if (f.kind === "check") return el("input", { id: id, type: "checkbox", checked: value === true });
    const i = el("input", { id: id, type: INPUT[f.kind] || "text", step: "any" });
    i.value = value === undefined || value === null ? "" : value;
    return i;
  }
  function drawForm(row) {
    const form = clear($("add"));
    spec.fields.forEach((f) => {
      form.appendChild(el("label", { for: "f-" + f.key, class: "f-" + f.kind }, [el("span", { text: f.name }), fieldInput(f, row ? row[f.key] : undefined)]));
    });
    const actions = el("div", { class: "actions" }, [el("button", { type: "submit", text: row ? "Save" : "Add " + spec.thing })]);
    if (row) actions.appendChild(el("button", { type: "button", class: "quiet", text: "Cancel", on: { click: () => { editing = ""; drawForm(); } } }));
    form.appendChild(actions);
  }
  function readForm() {
    const out = {};
    spec.fields.forEach((f) => {
      const i = $("f-" + f.key);
      out[f.key] = f.kind === "check" ? i.checked : i.value;
    });
    return out;
  }
  function shown(f, v) {
    if (f.kind === "check") return v === true ? "Yes" : "No";
    return v === undefined || v === null ? "" : String(v);
  }
  function cell(f, row) {
    const v = row[f.key];
    if (f.kind === "link" && v && /^https?:\/\//i.test(v)) return el("a", { href: v, target: "_blank", rel: "noopener noreferrer", text: v });
    return document.createTextNode(shown(f, v));
  }
  function rowActions(row) {
    return el("span", { class: "row-actions" }, [
      el("button", { type: "button", class: "quiet", text: "Edit", on: { click: () => { editing = row.id; drawForm(row); window.scrollTo(0, 0); } } }),
      el("button", { type: "button", class: "quiet", text: "Remove", on: { click: () => removeRow(row) } }),
    ]);
  }
  async function removeRow(row) {
    try { await call("DELETE", "/api/rows/" + row.id); say(""); await load(); } catch (e) { say(e.message, true); }
  }
  async function tick(row, f, on) {
    try { await call("PUT", "/api/rows/" + row.id, { [f.key]: on }); await load(); } catch (e) { say(e.message, true); }
  }
  function drawTable(box, rows) {
    const head = el("tr", {}, spec.fields.map((f) => el("th", { text: f.name })).concat([el("th", { text: "" })]));
    const body = rows.map((r) => el("tr", {}, spec.fields.map((f) => el("td", { "data-label": f.name }, [cell(f, r)])).concat([el("td", {}, [rowActions(r)])])));
    box.appendChild(el("div", { class: "scroll" }, [el("table", {}, [el("thead", {}, [head]), el("tbody", {}, body)])]));
  }
  function drawItems(box, rows) {
    const check = spec.fields.find((f) => f.kind === "check");
    rows.forEach((r) => {
      const words = spec.fields.filter((f) => f !== check && r[f.key] !== "" && r[f.key] !== undefined);
      const first = words[0];
      const card = el("article", { class: check && r[check.key] === true ? "done" : "" });
      const head = el("div", { class: "head" });
      if (check) head.appendChild(el("input", { type: "checkbox", "aria-label": check.name, checked: r[check.key] === true, on: { change: (ev) => tick(r, check, ev.target.checked) } }));
      head.appendChild(el("strong", { text: first ? shown(first, r[first.key]) : "" }));
      head.appendChild(rowActions(r));
      card.appendChild(head);
      words.slice(1).forEach((f) => {
        const p = el("p", {}, f.kind === "long" ? [] : [el("span", { class: "faint", text: f.name + ": " })]);
        p.appendChild(cell(f, r));
        card.appendChild(p);
      });
      box.appendChild(card);
    });
  }
  async function load() {
    const q = $("search").value;
    const got = await call("GET", "/api/rows" + (q ? "?search=" + encodeURIComponent(q) : ""));
    const box = clear($("rows"));
    if (!got.rows.length) box.appendChild(el("p", { class: "faint", text: q ? "Nothing matches that." : "No " + spec.things + " yet. Add the first one above." }));
    else if (spec.view === "table") drawTable(box, got.rows);
    else drawItems(box, got.rows);
    const total = got.total ? Object.entries(got.total).map(([k, v]) => {
      const f = spec.fields.find((x) => x.key === k);
      return (f ? f.name : k) + " total: " + Math.round(v * 100) / 100;
    }).join(", ") : "";
    $("total").textContent = total;
  }
  $("add").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      if (editing) await call("PUT", "/api/rows/" + editing, readForm());
      else await call("POST", "/api/rows", readForm());
      editing = "";
      drawForm();
      say("");
      await load();
    } catch (e) { say(e.message, true); }
  });
  let waiting = 0;
  $("search").addEventListener("input", () => { clearTimeout(waiting); waiting = setTimeout(() => load().catch((e) => say(e.message, true)), 200); });
  (async function start() {
    try {
      spec = await call("GET", "/api/spec");
      document.title = spec.title;
      $("title").textContent = spec.title;
      $("about").textContent = spec.about || "";
      $("search").placeholder = "Search " + spec.things;
      drawForm();
      await load();
    } catch (e) { say(e.message, true); }
  })();
})();
