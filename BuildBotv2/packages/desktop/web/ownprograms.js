/* Harvested from PuffPAIBuildBot@774e27a4:dashboard/app/dashboard/developer/_components/AddYourOwn.tsx (adapted): the
 * program and desktop app cards, as one tile with v2's plain classes.
 *
 * "A program on this computer" on Developer (D16, D138). PROPOSED, not tested by the owner.
 * Routes: GET and POST /v1/programs, DELETE and PATCH /v1/programs/<id>, POST /v1/programs/<id>/launch.
 *
 * The logic part (no DOM) runs under node too (tests/js/ownprograms.test.js); block() draws with window.Puff. */
(function (root) {
  "use strict";

  const WHAT_LINE = "Your bot reads the program's help, lists what it can do, and asks before each run unless you switch on Always allow.";
  const DESKTOP_LINE = "A program with a window, like Word or Excel: your bot can open it, and open your files in it.";

  /* Which field the words go in: anything with a slash is a path, anything else a name on this computer's PATH. */
  function addBody(kind, what, name) {
    const v = String(what || "").trim();
    const body = /[\\/]/.test(v) ? { kind: kind, path: v } : { kind: kind, command: v };
    if (name) body.name = String(name);
    return body;
  }

  /* One row as what the tile draws. A desktop program has Open; a command line one has the Always allow switch. */
  function rowView(p) {
    const desktop = p.kind === "desktop";
    return { id: p.id, name: String(p.name || ""), where: String(p.path || p.command || ""), kindWords: desktop ? "Has a window" : "Command line",
             open: desktop, canAlways: !desktop, always: p.always_allow === true,
             commands: (Array.isArray(p.commands) ? p.commands : []).slice(0, 12).map(function (c) { return String((c && c.command) || ""); }).filter(Boolean) };
  }

  /* The desktop programs on this computer not added yet. */
  function notAdded(installed, programs) {
    const have = new Set((programs || []).map(function (p) { return String(p.path || "").toLowerCase(); }));
    return (installed || []).filter(function (a) { return a && !have.has(String(a.path || "").toLowerCase()); });
  }

  function block() {
    const P = root.Puff, el = P.el, clear = P.clear;
    const t = P.tile({ id: "own-programs", icon: "terminal", title: "A program on this computer" });
    const list = el("div", { "data-own": "list" }), said = el("p", { class: "small", "data-own": "said" });
    const what = el("input", { type: "text", placeholder: "ffmpeg, or the program's full path", autocomplete: "off", spellcheck: "false" });
    const kind = el("select", { "data-own": "kind" }, [el("option", { value: "cli", text: "Command line" }), el("option", { value: "desktop", text: "Has a window" })]);
    const picks = el("select", { "data-own": "installed" });
    const pickRow = el("div", { class: "row", hidden: "hidden" });

    async function act(promise, done) {
      const r = await promise;
      said.textContent = r.status >= 400 || r.status === 0 ? P.said(r) : (r.data && r.data.warning) || done;
      if (r.status === 200) draw(said.textContent);
    }

    function rowEl(p) {
      const v = rowView(p);
      const parts = [el("h3", { text: v.name }), el("p", { class: "small muted", text: v.kindWords + ": " + v.where })];
      if (v.commands.length) parts.push(el("p", { class: "small muted", text: v.commands.join(", ") }));
      const buttons = el("div", { class: "row" });
      if (v.canAlways) {
        const box = el("input", { type: "checkbox", role: "switch", checked: v.always, "data-own": "always" });
        box.addEventListener("change", () => act(P.api("PATCH", "/v1/programs/" + encodeURIComponent(v.id), { always_allow: box.checked }),
          box.checked ? "Always allow is on for " + v.name + "." : "Always allow is off for " + v.name + "."));
        buttons.append(el("label", { class: "opt" }, [box, el("span", { text: "Always allow this program" })]));
      }
      if (v.open) buttons.append(el("button", { "data-own": "open", text: "Open", on: { click: () => act(P.api("POST", "/v1/programs/" + encodeURIComponent(v.id) + "/launch", {}), "Opening " + v.name + ".") } }));
      buttons.append(el("button", { "data-own": "remove", text: "Remove", on: { click: () => act(P.api("DELETE", "/v1/programs/" + encodeURIComponent(v.id)), "Removed " + v.name + ".") } }));
      parts.push(buttons);
      return el("div", { "data-own-row": v.id }, parts);
    }

    async function draw(line) {
      const r = await P.api("GET", "/v1/programs");
      if (r.status !== 200) { clear(list).append(el("p", { class: "fail", text: P.said(r) })); return; }
      clear(list).append(...(r.data.programs || []).map(rowEl));
      const left = notAdded(r.data.installed_desktop, r.data.programs);
      clear(picks).append(...left.map((a) => el("option", { value: a.path, text: a.name })));
      pickRow.hidden = !left.length;
      if (line) said.textContent = line;
    }

    const add = el("button", { class: "primary", "data-own": "add", text: "Add", on: { click: async () => {
      const r = await P.api("POST", "/v1/programs", addBody(kind.value, what.value));
      if (r.status !== 200) { said.textContent = P.said(r); return; }
      what.value = "";
      draw("Added " + r.data.program.name + ".");
    } } });
    const pick = P.pickPath ? el("button", { "data-own": "pick", text: "Pick a file", on: { click: async () => {
      const path = await P.pickPath("file", said);
      if (path) what.value = path;
    } } }) : null;
    pickRow.append(picks, el("button", { "data-own": "add-installed", text: "Add this app", on: { click: async () => {
      const chosen = picks.options[picks.selectedIndex];
      if (!chosen) return;
      const r = await P.api("POST", "/v1/programs", addBody("desktop", chosen.value, chosen.textContent));
      if (r.status !== 200) { said.textContent = P.said(r); return; }
      draw("Added " + r.data.program.name + ".");
    } } }));
    t.body.append(el("p", { class: "small muted", text: WHAT_LINE }), el("p", { class: "small muted", text: DESKTOP_LINE }),
      el("div", { class: "row" }, [what, kind, pick, add]), pickRow, said, list);
    draw();
    return t.node;
  }

  const api = { WHAT_LINE: WHAT_LINE, addBody: addBody, rowView: rowView, notAdded: notAdded, block: block };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PuffOwnPrograms = api;
})(typeof window !== "undefined" ? window : this);
